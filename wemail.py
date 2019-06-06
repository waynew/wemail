import io
import json
import os
import re
import smtplib
import subprocess
import sys
import tempfile
import time

from cmd import Cmd
from datetime import datetime
from email.header import decode_header
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.parser import BytesParser
from email.policy import EmailPolicy
from email.utils import getaddresses, formatdate, parsedate_to_datetime
from email.utils import parsedate_to_datetime
from getpass import getuser
from itertools import chain
from pathlib import Path
from textwrap import dedent

try:
    from mistletoe import markdown as commonmark
except ImportError:
    try:
        from commonmark import commonmark

        print("Got commonmark")
    except ImportError:
        commonmark = None

__version__ = "0.1.10"
POLICY = EmailPolicy(utf8=True)
CONFIG_PATH = Path("~/.wemailrc").expanduser()
_parser = BytesParser(_class=EmailMessage, policy=POLICY)


class WEmailError(Exception):
    pass


class WEmailMissingCommonmark(WEmailError):
    pass


class WEmailDeliveryError(WEmailError):
    pass


def prettynow():
    """
    Return current time as YYYYMMDDHHMMSS
    """
    return datetime.now().strftime("%Y%m%d%H%M%S")


def decode_subject(raw_subject):
    subject = ""
    for part in decode_header(raw_subject):
        data, encoding = part
        if encoding is None:
            try:
                subject += data
            except TypeError:
                subject += data.decode()
        else:
            subject += data.decode(encoding)
    return subject


def subjectify(*, msg):
    """
    Return the ``msg``'s subject, with just letters and numbers separated
    by hyphens.
    """
    subject = decode_subject(msg.get("Subject", ""))
    subject = re.sub("[^a-zA-Z0-9]+", "-", subject)
    return subject


def get_headers(*, mailfile):
    """
    Return the headers from an email file.
    """
    headfile = io.BytesIO()
    with mailfile.open("rb") as f:
        for line in f:
            if line == b"\n":  # found the newline
                break
            else:
                headfile.write(line)
    headfile.write(b"\n\n")
    headfile.seek(0)
    return _parser.parse(headfile)


def compose(*, editor, sender, to, default_headers=None):
    msg = EmailMessage(policy=POLICY)
    msg["From"] = sender
    # msg["To"] = to
    default_headers = default_headers or {}
    for header in default_headers:
        if header.lower() == "from":
            continue
        msg[header] = default_headers[header]
    with tempfile.NamedTemporaryFile() as email_file:
        email_file.write(msg.as_bytes(policy=POLICY))
        email_file.flush()
        edit(editor=editor, filename=email_file)
        email_file.seek(0)
        msg = _parser.parse(email_file)
    return msg


def edit(*, editor, filename):
    """
    Edit the provided file or ``filename`` with the provided ``editor``,
    and return the exit code.
    """
    try:
        filename = filename.name
    except AttributeError:
        pass  # It's probably a real filename
    return subprocess.call([editor, filename])


def commonmarkdown(plain_msg):
    """
    CommonMark-ify the provided msg. Return a multipart email with
    both text and HTML parts.
    """
    if commonmark is None:
        raise WEmailMissingCommonmark(
            "Cannot CommonMarkdown message. {sys.executable -m pip install --user mistletoe} and try again."
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


def pretty_recipients(msg):
    if msg.get("to"):
        yield "To: {}".format(", ".join(str(a) for a in msg["to"].addresses))
    if msg.get("cc"):
        yield "Cc: {}".format(", ".join(str(a) for a in msg["cc"].addresses))
    if msg.get("Bcc"):
        yield "Bcc: {}".format(", ".join(str(a) for a in msg["bcc"].addresses))


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
        try:
            smtp.send_message(
                msg, from_addr=sender, to_addrs=[addr for _, addr in recipients if addr]
            )
        except smtplib.SMTPDataError as e:
            raise WEmailDeliveryError(
                f"Failed to deliver {subjectify(msg=msg)!r} - {e.args[1].decode()!r}"
            ) from e


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
            if recipients:
                recipients += "\n"
            recipients += f"Cc: {', '.join(to)}"
        if bcc:
            if recipients:
                recipients += "\n"
            recipients += f"Bcc: {', '.join(to)}"
        lines = (
            self.msg.get_body(preferencelist=("related", "plain", "html"))
            .get_content()
            .split("\n")
        )
        if len(lines) > 20:
            lines = lines[:19] + ["... truncated"]
        body = "\n".join(lines)
        print(
            f"""
From: {self.msg["From"]}
Date: {self.msg.date}
{recipients or 'No recipients headers found???'}
Subject: {' '.join(self.msg.subject.split(chr(10)))}
            """.strip()
            + body
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
            date = parsedate_to_datetime(self.msg["Date"])
        except KeyError:
            date = "a day in the past"
        except TypeError:
            date = self.msg["Date"]
        else:
            date = date.strftime("%a, %B %d, %Y at %H:%M:%S%p %z").rstrip()

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

    def do_forward(self, line):
        """
        Forward email to recipient.
        """
        try:
            from_addr = self.msg.get("From").addresses[0]
            sender = from_addr.display_name or str(from_addr)
        except IndexError:
            sender = "Unknown"

        try:
            date = parsedate_to_datetime(self.msg["Date"])
        except KeyError:
            date = "a day in the past"
        except TypeError:
            date = self.msg["Date"]
        else:
            date = date.strftime("%a, %B %d, %Y").rstrip()

        fwd_msg = MIMEText(
            dedent(
                f"""
            ---------- Forwarded Message ----------
            From: {self.msg.get("From")}
            Date: {date}
            Subject: {self.msg.subject}
            """
            )
            + "\n".join(l for l in pretty_recipients(self.msg))
            + "\n"
            + self.msg.get_body(
                preferencelist=("related", "plain", "html")
            ).get_content()
        )
        fwd_msg["To"] = ", ".join(
            str(a) for s in self.msg.get_all("From") for a in s.addresses
        )
        if line == "all":
            fwd_msg["Cc"] = ", ".join(
                str(a)
                for s in chain(self.msg.get_all("Cc", []), self.msg.get_all("Bcc", []))
                for a in s.addresses
            )
        # TODO: Figure out which one of our addresses this was sent to -W. Werner, 2019-05-23
        addr = self.config.get("DEFAULT_FROM")
        fwd_msg["Subject"] = f"Fwd: {self.msg.subject}"
        for header in self.config[addr]["HEADERS"]:
            if header.lower() != "subject":
                fwd_msg[header] = self.config[addr]["HEADERS"][header]

        with tempfile.NamedTemporaryFile(suffix=".eml") as f:
            f.write(fwd_msg.as_bytes(policy=POLICY))
            f.flush()
            subprocess.call([self.config["EDITOR"], f.name])
            f.seek(0)
            fwd_msg = self.mailbox._parser.parse(f)

        print("=" * 20)
        print(str(fwd_msg))
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
            if fwd_msg.get("X-CommonMark", "").lower() in ("yes", "y", "true", "1"):
                fwd_msg = commonmarkdown(fwd_msg)
            send_message(
                smtp_host=self.config[addr]["SMTP_HOST"],
                smtp_port=self.config[addr]["SMTP_PORT"],
                use_tls=self.config[addr].get("SMTP_USE_TLS"),
                username=self.config[addr].get("SMTP_USERNAME"),
                password=self.config[addr].get("SMTP_PASSWORD"),
                msg=fwd_msg,
            )
            print("Sent!")

    # Aliases
    do_d = do_del = do_delete
    do_h = do_header
    do_p = do_parts
    do_q = do_quit
    do_s = do_m = do_move = do_save
    do_r = do_reply
    do_f = do_forward
    complete_s = complete_m = complete_move = complete_save


class CliMail(Cmd):
    def __init__(self, mailbox, config):
        super().__init__()
        self.config = config
        self.mailbox = mailbox
        self.editor = config["EDITOR"]
        self.sender = config.get("DEFAULT_FROM", getuser())

    @property
    def prompt(self):
        return f"WEmail - {self.mailbox.msg_count} {self.mailbox.curpath.name}> "

    def complete_cd(self, text, line, begidx, endidx):
        return [p.name for p in self.mailbox.path.glob(text + "*")]

    def complete_edit(self, text, line, begidx, endidx):
        return [p.name for p in self.mailbox.curpath.glob(text + "*")]

    def finish(self, msg):
        print(msg)
        if msg.get("X-CommonMark", "").lower() in ("yes", "y", "true", "1"):
            msg = commonmarkdown(msg)
        print("=" * 80)
        print("Finished composing")
        choice = input("[s]end now, [q]ueue, sa[v]e draft, [d]iscard? ").lower().strip()
        if choice == "v":
            draftname = self.mailbox.save_draft(msg=msg)
            print(f"Draft saved as {draftname}")
        elif choice == "s":
            name = self.mailbox.queue_for_delivery(msg)
            count = len(self.mailbox.outbox)
            if count < 2:
                print(f'Sending {msg["Subject"]!r}...', end="")
                sys.stdout.flush()
                self.mailbox.send_one(send_func=send_message, name=name)
                print("OK!")
            else:
                sendall = input(
                    f"{count} emails to send. Send all now? [Y/n]: "
                ).lower()
                if sendall in ("y", "yes"):
                    return self.do_sendall("")
                else:
                    print(f'Okay, just sending {msg["Subject"]}...', end="")
                    sys.stdout.flush()
                    self.mailbox.send_one(send_func=send_message, name=name)
                    print("OK!")
        elif choice == "q":
            name = self.mailbox.queue_for_delivery(msg)
            print(f"Message {name} queued for delivery")
        elif choice == "d":
            confirm = input("Really discard? [y/N]:").lower()
            if confirm == "y":
                print("Email discarded")
            else:
                draftname = self.mailbox.save_draft(msg=msg)
                print(f"Excellent! Draft saved as {draftname}")

    def do_quit(self, line):
        print("Okay bye!")
        return True

    def do_EOF(self, line):
        print()
        return self.do_quit(line)

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

    def do_edit(self, line):
        """
        Edit the file in the current directory.
        """
        file = self.mailbox.curpath / line
        edit(editor=self.editor, filename=str(file))

    def do_sendall(self, line):
        """
        Send all outgoing email now.
        """
        count = len(self.mailbox.outbox)
        if not count:
            print("No mail to send")
        else:
            print(f"Excellent! Sending {count} messages...")
            for status_code, subject, path in self.mailbox.send_all(
                send_func=send_message
            ):
                if status_code == 200:
                    print(f"Sent {subject!r}")
                else:
                    print(f"Failed to send. {subject} - {path}")
            print("Done!")

    def do_resume(self, line):
        """
        Resume editing a draft email.
        """
        count = len(self.mailbox.drafts)
        for i, mailfile in enumerate(self.mailbox.drafts, start=1):
            headers = get_headers(mailfile=mailfile)
            sender = headers["From"]
            subject = headers["Subject"]
            print(f"{i:>2}. {sender} - {subject}")
        valid = False
        while not valid:
            try:
                if line:
                    choice = line
                    line = None
                else:
                    choice = input(f"Resume which draft? [1-{count}]: ")
                choice = int(choice)
            except KeyboardInterrupt:
                print("\nCancelled!")
                return
            except ValueError:
                if choice.lower() in ("q", "quit", "c", "cancel"):
                    return
            if isinstance(choice, int) and 1 <= choice <= count:
                valid = True
                choice = choice - 1
            else:
                print(f"{choice!r} is not a valid choice")
        print("Editing draft...")
        draftname = self.mailbox.drafts[choice]
        edit(editor=self.config["EDITOR"], filename=str(draftname.resolve()))
        with draftname.open("rb") as draft:
            msg = _parser.parse(draft)
        draftname.unlink()
        self.finish(msg)

    def do_compose(self, line):
        """
        Compose a new email message using the default or optional role.
        """

        count = len(self.mailbox.drafts)
        if count:
            s = "" if count == 1 else "s"
            resume = input(f"{count} draft{s}, resume? [y/N]").lower()
            if resume in ("y", "yes"):
                do_resume("")
        else:
            msg = compose(
                editor=self.config["EDITOR"],
                sender=self.sender,
                to=line,
                default_headers=self.config[self.sender].get("HEADERS"),
            )
            self.finish(msg)

    def do_ls(self, line):
        if not line.strip():
            for msg in self.mailbox:
                print(f'{msg.path} - {msg.date} - {msg["From"]}')

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
                MsgPrompt(mailbox=self.mailbox, key=msg.key, config=self.config).onecmd(
                    line
                )
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
        paths = (self._newpath, self._curpath, self.queuepath, self.sentpath)
        for path in paths:
            path.mkdir(exist_ok=True, parents=True)

    def __iter__(self):
        for file in reversed(sorted(list(self.curpath.iterdir()))):
            yield self[file.name]

    def __getitem__(self, key):
        mailfile = self.curpath / key
        with mailfile.open("rb") as f:
            msg = self._parser.parse(f)
            msg.path = mailfile
            msg.key = f.name
            msg.date = None
            if msg["Date"]:
                msg.date = parsedate_to_datetime(msg["Date"])
            subject = ""
            msg.subject = decode_subject(msg["subject"])
            msg.recipients = list(
                chain(
                    msg.get_all("To", []), msg.get_all("Cc", []), msg.get_all("Bcc", [])
                )
            )
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
    def queuepath(self):
        return self.path / "outbox"

    @property
    def sentpath(self):
        return self.path / "sent"

    @property
    def msg_count(self):
        return sum(1 for _ in self.curpath.iterdir())

    @property
    def has_new(self):
        return any(self._newpath.iterdir())

    @property
    def outbox(self):
        return list(self.queuepath.iterdir())

    @property
    def drafts(self):
        return list(self.draftpath.iterdir())

    def send_all(self, *, send_func):
        for mail in self.outbox:
            try:
                yield self.send_one(send_func=send_func, name=mail.name)
            except WEmailDeliveryError as e:
                yield 400, str(e), str(mail.resolve())

    def send_one(self, *, send_func, name):
        mail = self.queuepath / name
        with mail.open("rb") as f:
            msg = self._parser.parse(f)
            sender = msg["From"]
            try:
                send_func(
                    smtp_host=self.config[sender]["SMTP_HOST"],
                    smtp_port=self.config[sender]["SMTP_PORT"],
                    use_tls=self.config[sender].get("SMTP_USE_TLS"),
                    username=self.config[sender].get("SMTP_USERNAME"),
                    password=self.config[sender].get("SMTP_PASSWORD"),
                    msg=msg,
                )
            except:
                raise
            else:
                sentname = self.sentpath / mail.name
                mail.rename(sentname)
                return 200, msg["Subject"], sentname

    def save_draft(self, *, msg, filename=None):
        name = filename or f"{prettynow()}-{subjectify(msg=msg)}"
        count = 0
        exists = True
        while exists:
            file = self.draftpath / (name + f'{count or ""}.eml')
            exists = file.exists()
        file.write_bytes(msg.as_bytes(policy=POLICY))
        return str(file.resolve())

    def queue_for_delivery(self, msg):
        with tempfile.NamedTemporaryFile(
            dir=self.queuepath, delete=False, suffix=".eml"
        ) as f:
            f.write(msg.as_bytes(policy=POLICY))
        return f.name

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
        "MAILDIR": os.environ.get("WEMAIL_DIR", "~/Maildir"),
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
        config["MAILDIR"] = sys.argv[1]
    config["MAILDIR"] = Path(config["MAILDIR"]).expanduser()

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
