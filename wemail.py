import json
import os
import smtplib
import subprocess
import sys
import tempfile
import time

from cmd import Cmd
from email.header import decode_header
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.parser import BytesParser
from email.policy import EmailPolicy
from email.utils import getaddresses, formatdate, parsedate_to_datetime
from email.utils import parsedate_to_datetime
from itertools import chain
from pathlib import Path
from textwrap import dedent

try:
    from commonmark import commonmark
except ImportError:
    commonmark = None

__version__ = "0.1.8"
POLICY = EmailPolicy(utf8=True)
CONFIG_PATH = Path("~/.wemailrc").expanduser()


class WEmailError(Exception):
    pass


class WEmailMissingCommonmark(WEmailError):
    pass


def commonmarkdown(plain_msg):
    """
    CommonMark-ify the provided msg. Return a multipart email with
    both text and HTML parts.
    """
    if commonmark is None:
        raise WEmailMissingCommonmark(
            "Cannot CommonMarkdown message. {sys.executable -m pip install --user commonmark} and try again."
        )
    html = commonmark(plain_msg.get_payload())
    msg = MIMEMultipart("alternative")
    for key in plain_msg.keys():
        for val in plain_msg.get_all(key):
            if key.lower() == "x-commonmark":
                continue
            msg[key] = val
    msg.attach(MIMEText(plain_msg.get_payload()))
    msg.attach(MIMEText(html, "html"))
    return msg


def send_message(
    *,
    msg,
    smtp_host="localhost",
    smtp_port=25,
    use_tls=False,
    use_smtps=False,
    username=None,
    password=None,
):
    sender = msg.get("From")
    recipients = getaddresses(
        chain(msg.get_all("To", []), msg.get_all("Cc", []), msg.get_all("Bcc", []))
    )
    if not msg.get("Date"):
        msg["Date"] = formatdate()
    SMTP = smtplib.SMTP_SSL if use_smtps else smtplib.SMTP
    with SMTP(host=smtp_host, port=smtp_port) as smtp:
        if use_tls:
            smtp.starttls()
        smtp.ehlo()
        if username or password:
            smtp.login(username, password)
        smtp.send_message(
            msg, from_addr=sender, to_addrs=[addr for _, addr in recipients]
        )


class MsgPrompt(Cmd):
    def __init__(self, *, mailbox, key, config):
        super().__init__()
        self.result = None
        self.config = config
        self.mailbox = mailbox
        self.msg = mailbox[key]
        self.do_header("")
        self.prompt = "action> "

    def get_part(self, part_num):
        part_num = part_num.strip()
        parts = []
        i = 1
        for part in self.msg.walk():
            content_type = part.get_content_type()
            if content_type.startswith("multipart/"):
                if not part_num:
                    print(content_type)
            else:
                parts.append(part)
                if not part_num:
                    print(f"\t{i}. {content_type}")
                i += 1
        choice = part_num or input(f"Edit which part? (1-{i-1}): ")
        if choice.strip():
            try:
                choice = int(choice) - 1
                return parts[choice]
            except ValueError:
                # Avoid growing memory for a typo
                parts.clear()
                return self.get_part("")

    def do_key(self, line):
        """
        Print the current message's key.
        """
        print(self.msg.key)

    def do_qq(self, line):
        """
        Abort further email processing.
        """
        self.result = "abort"
        return True

    def do_quit(self, line):
        return True

    def do_EOF(self, line):
        print()
        return self.do_quit(line)

    def do_D(self, line):
        """
        Delete immediately - no take backs.
        """
        return self.do_delete(" ")

    def do_delete(self, line):
        """
        Delete message with confirmation.
        """
        try:
            if line == " " or input("Really delete? [Y/n]: ").lower() not in (
                "n",
                "no",
            ):
                self.mailbox.delete(key=self.msg.key)
        except KeyboardInterrupt:
            print("Okay, never mind!")
        else:
            return True

    def do_save(self, line):
        self.mailbox.move_to(key=self.msg.key, folder=line.strip())
        print("Saved!")
        return True

    def complete_save(self, text, line, begidx, endidx):
        return [p.name for p in self.mailbox.path.glob(text + "*")]

    def do_print(self, line):
        """
        Print the message part.
        """
        # TODO: Check terminfo and pause after height lines -W. Werner, 2019-05-12
        part = self.get_part(line)
        if part is None:
            return
        print(part.get_payload(decode=True).decode())

    def do_body(self, line):
        """
        Print the message body - text/plain if it exists
        """
        print(
            self.msg.get_body(preferencelist=("related", "plain", "html")).get_content()
        )

    def do_p1(self, line):
        """
        View part 1 in the external editor.
        """
        return self.do_parts("1")

    def do_parts(self, line):
        part = self.get_part(line)
        if part is None:
            return
        with tempfile.NamedTemporaryFile(suffix=".eml") as f:
            f.write(part.get_payload(decode=True))
            f.flush()
            subprocess.call([self.config["EDITOR"], f.name])

    def do_raw(self, line):
        with tempfile.NamedTemporaryFile(suffix=".eml") as f:
            f.write(self.msg.as_bytes())
            f.flush()
            subprocess.call([self.config["EDITOR"], f.name])

    def do_header(self, line):
        """
        Display the message headers.
        """
        to = self.msg.get_all("To")
        cc = self.msg.get_all("Cc")
        bcc = self.msg.get_all("Bcc")
        recipients = ""
        if to:
            recipients += f"To: {', '.join(to)}"
        if cc:
            recipients += f"Cc: {', '.join(to)}"
        if bcc:
            recipients += f"Bcc: {', '.join(to)}"
        print(
            dedent(
                f"""\
        From: {self.msg["From"]}
        Date: {self.msg.date}
        {recipients or 'No recipients headers found???'}
        Subject: {' '.join(self.msg.subject.split(chr(10)))}
        """
            )
        )

    def do_reply(self, line):
        """
        Reply to the original sender. Add 'all' to reply to all.
        """
        try:
            from_addr = self.msg.get("From").addresses[0]
            sender = from_addr.display_name or str(from_addr)
        except IndexError:
            sender = "Unknown"

        try:
            date = parsedate_to_datetime(self.msg['Date'])
        except KeyError:
            date = "a day in the past"
        except TypeError:
            date = self.msg['Date']
        else:
            date = date.strftime('%a, %B %d, %Y at %H:%M:%S%p %z').rstrip()

        re_msg = MIMEText(
            f"On {date}, {sender} wrote:\n> "
            + self.msg.get_body(preferencelist=("related", "plain", "html"))
            .get_content()
            .replace("\n", "\n> ")
        )
        re_msg["To"] = ", ".join(
            str(a) for s in self.msg.get_all("From") for a in s.addresses
        )
        if line == "all":
            re_msg["Cc"] = ", ".join(
                str(a)
                for s in chain(self.msg.get_all("Cc", []), self.msg.get_all("Bcc", []))
                for a in s.addresses
            )
        # TODO: Figure out which one of our addresses this was sent to -W. Werner, 2019-05-23
        addr = self.config.get("DEFAULT_FROM")
        re_msg["Subject"] = f"Re: {self.msg.subject}"
        for header in self.config[addr]["HEADERS"]:
            if header.lower() != "subject":
                re_msg[header] = self.config[addr]["HEADERS"][header]

        with tempfile.NamedTemporaryFile(suffix=".eml") as f:
            f.write(re_msg.as_bytes(policy=POLICY))
            f.flush()
            subprocess.call([self.config["EDITOR"], f.name])
            f.seek(0)
            re_msg = self.mailbox._parser.parse(f)

        print("=" * 20)
        print(str(re_msg))
        print("=" * 20)
        choice = input("Send message? [Y/n]:")
        if choice.lower() not in ("n", "no"):
            abort_time = self.config.get("ABORT_TIMEOUT")
            while abort_time:
                print(f"\rSending in {abort_time}s", end="")
                sys.stdout.flush()
                time.sleep(1)
                abort_time -= 1
            print("\rSending message...", end="")
            sys.stdout.flush()
            if re_msg.get("X-CommonMark", "").lower() in ("yes", "y", "true", "1"):
                re_msg = commonmarkdown(re_msg)
            send_message(
                smtp_host=self.config[addr]["SMTP_HOST"],
                smtp_port=self.config[addr]["SMTP_PORT"],
                use_tls=self.config[addr].get("SMTP_USE_TLS"),
                username=self.config[addr].get("SMTP_USERNAME"),
                password=self.config[addr].get("SMTP_PASSWORD"),
                msg=re_msg,
            )
            print("Sent!")

    # Aliases
    do_d = do_del = do_delete
    do_h = do_header
    do_p = do_parts
    do_q = do_quit
    do_s = do_m = do_move = do_save
    complete_s = complete_m = complete_move = complete_save


class CliMail(Cmd):
    def __init__(self, mailbox, config):
        super().__init__()
        self.config = config
        self.mailbox = mailbox
        self.editor = config["EDITOR"]

    @property
    def prompt(self):
        return f"WEmail - {self.mailbox.msg_count} {self.mailbox.curpath.name}> "

    def edit(self, filename):
        try:
            filename = filename.name
        except AttributeError:
            pass  # It's probably a real filename
        subprocess.call([self.editor, filename])

    def do_quit(self, line):
        print("Okay bye!")
        return True

    def do_EOF(self, line):
        print()
        return self.do_quit(line)

    def complete_cd(self, text, line, begidx, endidx):
        return [p.name for p in self.mailbox.path.glob(text + "*")]

    def do_cd(self, line):
        line = line.lstrip(".")
        if line:
            newpath = self.mailbox.path / line
            if not newpath.is_dir():
                print(f"Error, no directory {newpath}")
            else:
                self.mailbox.curpath = newpath
        else:
            self.mailbox.curpath = self.mailbox._curpath

    def do_compose(self, line):
        """
        Compose a new email message using the default or optional role.
        """
        from_addr = self.config.get(line, self.config.get("DEFAULT_FROM"))
        msg = MIMEText("")
        msg["To"] = ""
        for header in self.config[from_addr]["HEADERS"]:
            msg[header] = self.config[from_addr]["HEADERS"][header]
        self.mailbox.draftpath.mkdir(exist_ok=True, parents=True)
        with tempfile.NamedTemporaryFile(
            dir=self.mailbox.draftpath, suffix=".eml", delete=False
        ) as f:
            f.write(msg.as_bytes(policy=POLICY))
            f.flush()
            self.edit(f)
            f.seek(0)
        with open(f.name, "rb") as f:
            msg = self.mailbox._parser.parse(f)
            print(msg)
            if msg.get("X-CommonMark", "").lower() in ("yes", "y", "true", "1"):
                msg = commonmarkdown(msg)
        choice = input("Send email? [Y/n]:")
        if choice.lower() in ("n", "no"):
            print(f"Draft saved as {f.name}")
        else:
            print("Sending message...", end="")
            sys.stdout.flush()
            send_message(
                smtp_host=self.config[from_addr]["SMTP_HOST"],
                smtp_port=self.config[from_addr]["SMTP_PORT"],
                use_tls=self.config[from_addr].get("SMTP_USE_TLS"),
                username=self.config[from_addr].get("SMTP_USERNAME"),
                password=self.config[from_addr].get("SMTP_PASSWORD"),
                msg=msg,
            )
            Path(f.name).unlink()
            print(" Sent!")

    def do_ls(self, line):
        if not line.strip():
            for msg in self.mailbox:
                print(msg.date, msg["From"])

    def do_proc(self, line):
        """
        Process emails one at a time.

        To interrupt processing, use qq.
        """
        if line:
            try:
                line = int(line)
                MsgPrompt(
                    mailbox=self.mailbox, key=self.mailbox[line], config=self.config
                ).cmdloop()
            except ValueError:
                msg = next(iter(self.mailbox))
                MsgPrompt(
                    mailbox=self.mailbox, key=msg.key, config=self.config
                ).onecmd(line)
        else:
            for msg in self.mailbox:
                p = MsgPrompt(mailbox=self.mailbox, key=msg.key, config=self.config)
                p.cmdloop()
                if p.result == "abort":
                    return

    def do_check(self, line):
        """
        Check for new mail.
        """
        count = self.mailbox.check_new()
        print(f'{count} new message{"s" if count != 1 else ""}')

    def do_version(self, line):
        """
        Display WEmail version info.
        """
        print(f"{__version__}")

    do_q = do_quit
    do_c = do_compose


class WeMaildir:
    def __init__(self, config):
        self.config = config
        self.path = Path(config["MAILDIR"])
        self.curpath = self._curpath
        self._parser = BytesParser(_class=EmailMessage, policy=POLICY)

    def __iter__(self):
        for file in reversed(sorted(list(self.curpath.iterdir()))):
            yield self[file.name]

    def __getitem__(self, key):
        mailfile = self.curpath / key
        with mailfile.open("rb") as f:
            msg = self._parser.parse(f)
            msg.key = f.name
            msg.date = None
            if msg["Date"]:
                msg.date = parsedate_to_datetime(msg["Date"])
            subject = ""
            for part in decode_header(msg["subject"]):
                data, encoding = part
                if encoding is None:
                    try:
                        subject += data
                    except TypeError:
                        subject += data.decode()
                else:
                    subject += data.decode(encoding)
            msg.subject = subject
            return msg

    @property
    def _newpath(self):
        return self.path / "new"

    @property
    def _curpath(self):
        return self.path / "cur"

    @property
    def draftpath(self):
        return self.path / "draft"

    @property
    def msg_count(self):
        return sum(1 for _ in self.curpath.iterdir())

    @property
    def has_new(self):
        return any(self._newpath.iterdir())

    def check_new(self):
        """
        Check for mail in /new.

        If new mail is found, move it to /cur and return True.
        If no new mail is found, return False.
        """
        count = 0
        for file in self._newpath.iterdir():
            count += 1
            newname = self._curpath / file.name
            file.rename(newname)
        return count

    def move_to(self, *, key, folder):
        """
        Move the message given by ``key`` to the provided ``folder``.
        """
        f = self.curpath / key
        newname = self.path / folder / f.name
        newname.parent.mkdir(parents=True, exist_ok=True)
        f.rename(newname)

    def delete(self, key):
        """
        Delete the message given by ``key``.
        """
        (self.curpath / key).unlink()


def update():
    """
    Update wemail if updates are available.
    """
    cmd = [sys.executable, "-m", "pip", "search", "wemail"]
    print("Checking for updates...")
    output = subprocess.check_output(cmd).decode().split("\n")
    installed = next(line for line in output if line.strip().startswith("INSTALLED"))
    latest = next((line for line in output if line.strip().startswith("LATEST")), None)
    if installed and not installed.endswith(" (latest)"):
        latest_version = latest.rsplit(" ", maxsplit=1)[-1]
        choice = input(f"New version {latest_version} available, upgrade? [Y/n]:")
        if choice not in ("n", "no"):
            cmd = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--user",  # There may be a better way
                "--upgrade",
                "wemail",
            ]
            try:
                print(f"Upgrading to {latest_version}...")
                output = subprocess.check_output(cmd)
            except subprocess.CalledProcessError as e:
                print("Error upgrading wemail:", e)
                sys.exit()
            else:
                # No reason to check for updates *again*
                env = os.environ.copy()
                del env["WEMAIL_CHECK_FOR_UPDATES"]
                subprocess.run([sys.executable, "-m", "wemail", *sys.argv[1:]], env=env)
                return
        else:
            print("Okay! Not upgrading...")


def do_it():  # Shia LeBeouf!
    config = {
        "CHECK_FOR_UPDATES": os.environ.get("WEMAIL_CHECK_FOR_UPDATES", False),
        "EDITOR": os.environ.get("EDITOR", os.environ.get("VISUAL", "nano")),
        "MAILDIR": Path(os.environ.get("WEMAIL_DIR", "~/Maildir")),
    }
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open() as cf:
            config.update(json.load(cf))

    if config["CHECK_FOR_UPDATES"]:
        update()

    if "--version" in sys.argv or "-v" in sys.argv:
        print(__version__)
        return
    elif len(sys.argv) > 1:
        config["MAILDIR"] = Path(sys.argv[1])

    if not config["MAILDIR"].exists():
        sys.exit(f'Maildir {str(config["MAILDIR"])!r} does not exist.')

    mailbox = WeMaildir(config)
    # TODO: Try to import curses and do a curses UI -W. Werner, 2019-05-10
    # It should basically be a clone(ish) of Alpine, which I love, but isn't
    # quite as awesome as I really want.
    # The exceptional case shoule be running the CliMail - or if there was
    # an option passed on the command line
    try:
        CliMail(mailbox, config=config).cmdloop()
    except KeyboardInterrupt:
        print()
        print("^C caught, goodbye!")


if __name__ == "__main__":
    do_it()
