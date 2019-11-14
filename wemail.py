import ast
import io
import json
import mimetypes
import os
import re
import shutil
import smtplib
import subprocess
import sys
import tempfile
import time

from cmd import Cmd
from datetime import datetime
from email.header import decode_header
from email.message import EmailMessage
from email.mime.application import MIMEApplication
from email.mime.audio import MIMEAudio
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.parser import BytesParser
from email.policy import EmailPolicy
from email.utils import (
    getaddresses,
    formatdate,
    parsedate_to_datetime,
    formataddr,
    parseaddr,
)
from getpass import getuser
from itertools import chain
from pathlib import Path
from textwrap import dedent

try:
    from mistletoe import markdown as commonmark
except ImportError as e:
    try:
        from commonmark import commonmark

        print("Got commonmark")
    except ImportError:
        commonmark = None

__version__ = "0.2.0"
POLICY = EmailPolicy(utf8=True)
CONFIG_PATH = Path("~/.wemailrc").expanduser()
_parser = BytesParser(_class=EmailMessage, policy=POLICY)
SKIPPED_HEADERS = ("To", "Cc", "DKIM-Signature", "Message-ID", "Subject")
DEFAULT_HEADERS = {"From": "", "To": "", "Subject": ""}


class WEmailError(Exception):
    pass


class WEmailMissingCommonmark(WEmailError):
    pass


class WEmailDeliveryError(WEmailError):
    pass


class Message:
    def __init__(self, *, config=None, draftdir=None, headers=None):
        config = config or {}
        draftdir = draftdir or config.get("draftdir", ".")
        self.filename = Path(draftdir, f"{prettynow()}-new.eml")
        self.config = config
        self._cleanup = True
        self._headers = headers or {}

    def __enter__(self):
        self.filename.touch()
        msg = EmailMessage(policy=POLICY)
        for header in self._headers:
            msg[header] = self._headers[header]
        self.filename.write_bytes(msg.as_bytes())
        return self

    def __exit__(self, type, value, traceback):
        if traceback is None and self._cleanup:
            self.filename.unlink()

    @property
    def msg(self):
        with self.filename.open(mode="rb") as f:
            return _parser.parse(f)

    def save(self, *, filename=None):
        self._cleanup = False
        if filename:
            self.filename.rename(filename)

    def send(self, smtp):
        ...


def chunkstring(text, length=80):
    '''
    Chunk string into fixed length strings. If string, or leftover piece,
    is smaller than ``length``, return string.
    '''
    start = 0
    while start == 0 or start+length <= len(text):
        yield text[start:start+length]
        start = start+length


def action_prompt():
    choice = input("[s]end now, [q]ueue, sa[v]e draft, [d]iscard? ").lower().strip()
    if choice not in ("s", "q", "v", "d"):
        print(f"{choice!r} not a valid input.")
        return action_prompt()
    return choice


def take_action(*, mailbox, original_msg, editor, abort_timeout, draftdir):
    with Message(draftdir=draftdir) as draft:
        draft.filename.write_bytes(original_msg.as_bytes())
        edit(editor=editor, filename=draft.filename)
        draft.filename.write_bytes(attachify(commonmarkdown(draft.msg)).as_bytes())
        draftname = str(draft.filename)

        choice = action_prompt()
        if choice == "v":
            draft.save()
            print(f"Draft saved as {draftname}")
        elif choice == "q":
            name = mailbox.queue_for_delivery(draft.msg)
            print(f"Message {name} queued for delivery")
        elif choice == "d":
            confirm = input("Really discard? [y/N]:").lower()
            if confirm == "y":
                print("Email discarded")
            else:
                draft.save()
                print(f"Excellent! Draft saved as {draftname}")
        elif choice == "s":
            name = mailbox.queue_for_delivery(draft.msg)
            count = len(mailbox.outbox)
            if count < 2:
                abort_time = abort_timeout
                while abort_time:
                    print(f"\rSending in {abort_time}s - ^C to cancel...", end="")
                    sys.stdout.flush()
                    time.sleep(1)
                    abort_time -= 1
                print()
                print(f'Sending {draft.msg["Subject"]!r}...', end="")
                sys.stdout.flush()
                mailbox.send_one(send_func=send_message, name=name)
                print("OK!")
            else:
                sendall = input(
                    f"{count} emails to send. Send all now? [Y/n]: "
                ).lower()
                if sendall in ("y", "yes"):
                    count = len(mailbox.outbox)
                    if not count:
                        print("No mail to send")
                    else:
                        print(f"Excellent! Sending {count} messages...")
                        for status_code, subject, path in mailbox.send_all(
                            send_func=send_message
                        ):
                            if status_code == 200:
                                print(f"Sent {subject!r}")
                            else:
                                print(f"Failed to send. {subject} - {path}")
                    print("Done!")
                else:
                    print(f'Okay, just sending {draft.msg["Subject"]}...', end="")
                    sys.stdout.flush()
                    mailbox.send_one(send_func=send_message, name=name)
                    print("OK!")


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


def compose(*, draft_dir, editor, sender, to, default_headers=None):
    msg = EmailMessage(policy=POLICY)
    default_headers = {
        h.lower(): default_headers[h] for h in default_headers or {} if h.strip()
    }
    default_headers["from"] = sender
    default_headers["to"] = to or default_headers["to"]
    headers = list(default_headers)
    presets = ["from", "to"]
    for val in presets:
        headers.remove(val)
    for header in presets + headers:
        msg[header.title()] = default_headers[header]
    # TODO: Rewrite this compose process. It should probably be a context manager -W. Werner, 2019-06-13
    # The current problem is that if things crash, we're out of luck, the
    # message is lost. We don't want that. So we need to change this so that
    # when a message is composed, we can try to do things with the resulting
    # message, but if something unexpected happens we /should not lose that message/!!!
    #
    # I think it should look like this:
    # with compose(...) as msg:
    #    ...
    #    send/save/discard/queue
    #
    with tempfile.NamedTemporaryFile(suffix=".eml") as email_file:
        email_file.write(msg.as_bytes(policy=POLICY))
        email_file.flush()
        edit(editor=editor, filename=email_file.name)
        email_file.seek(0)
        msg = _parser.parse(email_file)
    return msg


def edit(*, editor, filename):
    """
    Edit the provided file or ``filename`` with the provided ``editor``,
    and return the exit code.
    """
    try:
        filename = str(filename.resolve())
    except AttributeError:
        pass  # It's probably a real filename
    return subprocess.call([editor, filename])


def commonmarkdown(plain_msg):
    """
    CommonMark-ify the provided msg. Return a multipart email with
    both text and HTML parts.
    """

    if "X-CommonMark" not in plain_msg:
        return plain_msg
    msg = _parser.parsebytes(plain_msg.as_bytes())
    del msg["X-CommonMark"]

    msg.make_alternative()
    msg.add_alternative(commonmark(plain_msg.get_payload()), subtype="html")
    return msg


def attachify(msg):
    """
    Seek for attachment headers. If any exist, attach files. If ``; name=``
    is present in the header, use that name. Otherwise, use the original
    filename. Paths will be relative to the current directory, unless
    they're absolute.

    ``; inline=true`` must be set to inline the attachment.

    If attachment filename does not exist, raise WEmailAttachmentNotFound.
    """
    related_msg = _parser.parsebytes(msg.as_bytes())
    related_msg.make_mixed()
    related_msg.preamble = "This is a MIME-formatted multi-part message."
    attachments = related_msg.get_all("Attachment")

    if attachments is None:
        return msg
    del related_msg["Attachment"]

    attachment_ids = set()
    for attachment in attachments:
        filename, *extra = attachment.split(";")
        filename = Path(filename).resolve()
        name = filename.name
        type_, encoding = mimetypes.guess_type(filename.name)
        disposition = "attachment"
        for bit in extra:
            key, _, val = bit.strip().partition("=")
            key = key.strip()
            val = val.strip()
            if key.lower() == "inline" and val.lower() == "true":
                disposition = "inline"
            elif key.lower() in ("name", "filename"):
                name = ast.literal_eval(val)
        if type_ is None or type_.startswith("application/"):
            part = MIMEApplication(filename.read_bytes(), policy=POLICY, name=name)
        elif type_.startswith("text/"):
            part = MIMEText(filename.read_text(), policy=POLICY)
        elif type_.startswith("audio/"):
            part = MIMEAudio(filename.read_bytes(), policy=POLICY, name=name)
        elif type_.startswith("image/"):
            part = MIMEImage(filename.read_bytes(), policy=POLICY, name=name)
        part.add_header("Content-Disposition", disposition, filename=name)
        part.add_header("Content-ID", f"<{name}>")
        part.add_header("X-Attachment-Id", name)
        related_msg.attach(part)
    return related_msg


def replyify(*, msg, sender, reply_all=False, keep_attachments=False):
    """
    Take a msg and return a reply message. Default address is `Reply-To`,
    followed by `From`. If `all_recipients` is ``True``, add every
    recipient to the list(s) they were in.
    """
    if not keep_attachments:
        reply = _parser.parsebytes(msg.get_body(("plain", "html")).as_bytes())
    else:
        reply = _parser.parsebytes(msg.as_bytes())

    for field in SKIPPED_HEADERS:
        try:
            del reply[field]
        except KeyError:
            pass
    if reply_all:
        to_recipients = getaddresses(msg.get_all("From", []) + msg.get_all("To", []))
        cc_recipients = getaddresses(msg.get_all("Cc", []))
        reply["To"] = ", ".join(formataddr(addr) for addr in to_recipients)
        reply["Cc"] = ", ".join(formataddr(addr) for addr in cc_recipients)
    else:
        for fromaddr in msg.get_all("Reply-To", msg.get_all("From", [])):
            reply["To"] = fromaddr

    try:
        del reply["From"]
    except KeyError:
        # Replying when you don't have an original sender? That's weird
        pass
    finally:
        reply["From"] = sender

    try:
        from_addr = msg.get("From").addresses[0]
        msg_sender = from_addr.display_name or str(from_addr)
    except IndexError:
        msg_sender = "Unknown"

    try:
        date = parsedate_to_datetime(msg["Date"])
    except KeyError:
        date = "a day in the past"
    except TypeError:
        date = msg["Date"]
    else:
        date = date.strftime("%a, %B %d, %Y at %H:%M:%S%p %z").rstrip()

    try:
        body = reply.get_body().get_payload()
    except AttributeError:
        body = ""
    reply.get_body().set_content(
        f"On {date}, {msg_sender} wrote:\n> " + body.replace("\n", "\n> ")
    )
    reply["Subject"] = "Re: " + msg.get("subject", "")
    return reply


def forwardify(*, msg, sender, keep_attachments=False):
    if not keep_attachments:
        fwd_msg = _parser.parsebytes(msg.get_body(("plain", "html")).as_bytes())
    else:
        fwd_msg = _parser.parsebytes(msg.as_bytes())

    for header in SKIPPED_HEADERS:
        try:
            del fwd_msg[header]
        except KeyError:
            pass
    try:
        date = parsedate_to_datetime(msg["Date"])
    except KeyError:
        date = "a day in the past"
    except TypeError:
        date = msg["Date"]
    else:
        date = date.strftime("%a, %B %d, %Y at %H:%M:%S%p %z").rstrip()
    try:
        del fwd_msg["From"]
    except KeyError:
        pass  # How are you forwarding an email that came from nobody?
    fwd_msg["From"] = sender
    fwd_msg["To"] = ""
    fwd_msg["Subject"] = "Fwd: " + msg.get("Subject", "")
    fwd_msg.get_body(("plain", "html")).set_content(
        dedent(
            f"""
        ---------- Forwarded Message ----------
        From: {msg.get("From")}
        Date: {date}
        Subject: {msg.get('Subject')}
        """
        )
        + "\n".join(l for l in pretty_recipients(msg))
        + "\n\n"
        + msg.get_body(preferencelist=("related", "plain", "html")).get_content()
    )
    return fwd_msg


def pretty_recipients(msg):
    if msg.get("to"):
        yield "To: {}".format(", ".join(str(a) for a in msg["to"].addresses))
    if msg.get("cc"):
        yield "Cc: {}".format(", ".join(str(a) for a in msg["cc"].addresses))
    if msg.get("Bcc"):
        yield "Bcc: {}".format(", ".join(str(a) for a in msg["bcc"].addresses))


def recipients_list(msg):
    return getaddresses(
        msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Bcc", [])
    )


def get_sender(*, msg, config):
    """
    Get the new sender, based on the recipients from the message.

    Suitable for use in ``msg['From']``.
    """
    recipients = set()
    all_recipients = recipients_list(msg)
    if len(all_recipients) > 1:
        for name, addr in all_recipients:
            if addr in config:
                recipients.add((name, addr))
        # TODO: This requires manual removal of the extra "From" addresses -W. Werner, 2019-06-19
        # We should prompt and ask the user which one they want to use...
        sender = ", ".join(formataddr(a) for a in recipients)
    else:
        sender = formataddr(all_recipients[0])
    return sender


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

    def do_links(self, line):
        '''
        Print a list of links found in the message. If an argument
        is provided it's interpreted as the part of the message to
        search through, for multipart messages.
        '''
        if line:
            part = self.get_part(line).get_payload(decode=True).decode()
        else:
            part = self.msg.get_body(preferencelist=("related", "plain", "html")).get_content()
        results = re.findall('[a-zA-Z0-9]*://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', part)
        for result in results:
            print(result)

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
        self.mailbox.move_to(key=self.msg.key, folder=line.strip() or 'saved-mail')
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
        termsize = shutil.get_terminal_size()
        import textwrap
        body = self.msg.get_body(preferencelist=("related", "plain", "html")).get_content().splitlines()

        count = 0
        for line in body:
            # TODO: Make max width configurable -W. Werner, 2019-07-24
            print(line)
            continue
            for part in chunkstring(line, length=min(termsize.columns, 120)):
                count += 1
                print(part)
                if count+5 >= termsize.lines:
                    cont = input('<Enter> to continue...')
                    if cont.lower() in ('q', 'c', 'cancel'):
                        print('Skipping')
                        return
                    count = 0

    def do_p1(self, line):
        """
        View part 1 in the external editor.
        """
        return self.do_parts("1")

    def do_ext(self, line):
        """
        View message part in external viewer/program.

        Usage: ext PROGRAM PART

        Example: ext lynx 2
        """
        program, _, part = line.partition(' ')
        program = program.strip()
        part = part.strip()
        if not (program or part):
            print('Usage: ext PROGRAM PART')
        part = self.get_part(part)
        filename = part.get_filename() or ''
        extension = mimetypes.guess_extension(part.get_content_type()) or '.txt'
        with tempfile.NamedTemporaryFile(prefix=filename, suffix=extension) as f:
            f.write(part.get_payload(decode=True))
            f.flush()
            subprocess.call([program, f.name])

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
        termsize = shutil.get_terminal_size()
        try:
            lines = (
                self.msg.get_body(preferencelist=("related", "plain", "html"))
                .get_content()
                .split("\n")
            )
            lines = list(chain(*(chunkstring(line, length=min(termsize.columns, 120)) for line in lines)))
        except (AttributeError, KeyError):
            lines = list(f"- {part.get_content_type()}" for part in self.msg.walk())
            if not lines:
                lines.append("\tNo parts")
            lines.insert(0, "No message body. Parts:")
        offset = 10
        if len(lines)+offset > termsize.lines:
            lines = lines[:termsize.lines-offset] + ["... truncated"]
        body = "\n".join(lines)
        print(
            f"""\
From: {self.msg["From"]}
Date: {self.msg.date}
{recipients or 'No recipients headers found???'}
Subject: {' '.join(self.msg.subject.split(chr(10)))}
            """.strip()
            + "\n\n"
            + body
        )

    def do_reply(self, line):
        """
        Reply to the original sender. Add 'all' to reply to all.
        """
        sender = get_sender(msg=self.msg, config=self.config)
        reply_all = input("Reply All? [y/N]: ").lower().strip() in ("y", "yes")
        take_action(
            original_msg=replyify(msg=self.msg, sender=sender, reply_all=reply_all),
            editor=self.config["EDITOR"],
            mailbox=self.mailbox,
            abort_timeout=self.config["ABORT_TIMEOUT"],
            draftdir=self.mailbox.draftpath,
        )

    def do_forward(self, line):
        """
        Forward email to recipient.
        """
        keep_attachments = False
        if any(self.msg.iter_parts()):
            keep_attachments = input("Include attachments? [y/N]: ").lower() in (
                "y",
                "Yes",
            )

        sender = get_sender(msg=self.msg, config=self.config)
        take_action(
            original_msg=forwardify(
                msg=self.msg, sender=sender, keep_attachments=keep_attachments
            ),
            editor=self.config["EDITOR"],
            mailbox=self.mailbox,
            abort_timeout=self.config["ABORT_TIMEOUT"],
            draftdir=self.mailbox.draftpath,
        )

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
        print("]0;wemail", end="\a", flush=True)
        super().__init__()
        self.config = config
        self.mailbox = mailbox
        self.editor = config["EDITOR"]
        self.sender = config.get("DEFAULT_FROM", getuser())
        self.address_book = config.get("ADDRESS_BOOK", {})

    @property
    def prompt(self):
        return f"WEmail - {self.mailbox.msg_count} {self.mailbox.curpath.name}> "

    def complete_cd(self, text, line, begidx, endidx):
        return [p.name for p in self.mailbox.path.glob(text + "*")]

    def complete_edit(self, text, line, begidx, endidx):
        return [p.name for p in self.mailbox.curpath.glob(text + "*")]

    def complete_compose(self, text, line, begidx, endidx):
        return [
            self.address_book[alias]
            for alias in self.address_book
            if alias.lower().startswith(text.lower())
            or text.lower() in self.address_book[alias].lower()
        ]

    # TODO: Unify this finishing functionality -W. Werner, 2019-06-19
    def finish(self, msg):
        raw_msg = msg
        print(msg)
        if msg.get("X-CommonMark", "").lower() in ("yes", "y", "true", "1"):
            msg = commonmarkdown(msg)
        msg = attachify(msg)
        print("=" * 80)
        print("Finished composing")
        choice = input("[s]end now, [q]ueue, sa[v]e draft, [d]iscard? ").lower().strip()
        if choice == "v":
            draftname = self.mailbox.save_draft(msg=raw_msg)
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
                    if count > 1:
                        choice = input(f"Resume which draft? [1-{count}]: ")
                    choice = 1
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
                return self.do_resume("")
        msg = compose(
            draft_dir=self.mailbox.draftpath,
            editor=self.config["EDITOR"],
            sender=self.sender,
            to=line,
            default_headers=self.config[self.sender].get("HEADERS"),
        )
        self.finish(msg)

    def do_ls(self, line):
        if not line.strip():
            for msg in self.mailbox:
                line = f'{msg.date} - {msg["From"]} - {msg["subject"]}'
                # TODO: use terminal length instead -W. Werner, 2019-11-14
                if len(line) > 80:
                    line = f'{line[:77]}...'
                print(line)

    def do_filter(self, line):
        filter_cmds = self.config.get('filters', [])
        for msg_path in self.mailbox.curpath.iterdir():
            for filter_cmd in filter_cmds:
                if not filter_cmd: continue  # Make sure we have a filter
                try:
                    result = subprocess.run(filter_cmd+[msg_path])
                except Exception as e:
                    print('Error filtering', filter_cmd, e)

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

    def do_update(self, line):
        return update()

    do_q = do_quit
    do_c = do_compose
    complete_c = complete_compose


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
            config = self.config.get(
                sender, self.config.get(self.config.get("DEFAULT_FROM"))
            )
            try:
                send_func(
                    smtp_host=config["SMTP_HOST"],
                    smtp_port=config["SMTP_PORT"],
                    use_tls=config.get("SMTP_USE_TLS"),
                    username=config.get("SMTP_USERNAME"),
                    password=config.get("SMTP_PASSWORD"),
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
                try:
                    del env["WEMAIL_CHECK_FOR_UPDATES"]
                except KeyError:
                    pass  # Huh. Okay *shrugs*
                subprocess.run([sys.executable, "-m", "wemail", *sys.argv[1:]], env=env)
                return True
        else:
            print("Okay! Not upgrading...")
    else:
        print("All up-to-date! Sweet!")


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
