import argparse
import ast
import collections
import quopri
import io
import json
import logging
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
from datetime import timezone
from email.header import decode_header
from email.message import EmailMessage
from email.mime.application import MIMEApplication
from email.mime.audio import MIMEAudio
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.parser import BytesHeaderParser
from email.parser import BytesParser
from email.policy import EmailPolicy
from email.utils import formataddr
from email.utils import format_datetime
from email.utils import getaddresses
from email.utils import parseaddr
from email.utils import parsedate_to_datetime
from getpass import getuser
from itertools import chain
from pathlib import Path
from textwrap import dedent

log = logging.getLogger("wemail")

try:
    from mistletoe import markdown as commonmark

    log.debug("Got mistletoe")
except ImportError as e:  # pragma: no cover
    try:
        from commonmark import commonmark

        log.debug("Got commonmark")
    except ImportError:

        def commonmark(msg):
            return msg

        print(
            "No commonmark/markdown library installed. Install mistletoe"
            " or commonmark"
        )

__version__ = "2020.03.27.1"
POLICY = EmailPolicy(utf8=True)
CONFIG_PATH = Path("~/.wemailrc").expanduser()
_parser = BytesParser(_class=EmailMessage, policy=POLICY)
_header_parser = BytesHeaderParser(policy=POLICY)
DEFAULT_HEADERS = {"From": "", "To": "", "Subject": ""}
DISPLAY_HEADERS = ("From", "To", "CC", "Reply-to", "List-Id", "Date", "Subject")
EmailTemplate = collections.namedtuple("EmailTemplate", "name,content")
LOCAL_TZ = datetime.now(timezone.utc).astimezone().tzinfo


class WEmailError(Exception):
    pass


class WEmailDeliveryError(WEmailError):
    pass


def make_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=argparse.FileType("r"),
        default=Path("~/.wemailrc").expanduser().resolve().open("r"),
    )
    parser.add_argument(
        "--version", action="store_true", default=False, help="Print the version."
    )
    subparsers = parser.add_subparsers()
    new_parser = subparsers.add_parser("new", help="Create new email from templates.")
    new_parser.set_defaults(action="new")
    new_parser.add_argument(
        "template_number",
        type=int,
        nargs="?",
        default=None,
        help="Template number to use.",
    )

    send_parser = subparsers.add_parser("send", help="Send specific email.")
    send_parser.set_defaults(action="send")
    send_parser.add_argument("mailfile", type=Path)

    sendall_parser = subparsers.add_parser(
        "send_all", help="Send all emails in outbox."
    )
    sendall_parser.set_defaults(action="send_all")

    check_parser = subparsers.add_parser("check", help="Check for new email.")
    check_parser.set_defaults(action="check")

    filter_parser = subparsers.add_parser(
        "filter", help="Run filters against the inbox or specified folder."
    )
    filter_parser.set_defaults(action="filter")
    filter_parser.add_argument(
        "folder", nargs="?", help="Filter messages in inbox or specified folder."
    )

    reply_parser = subparsers.add_parser(
        "reply", help="Reply to reply-to or sender of an email."
    )
    reply_parser.set_defaults(action="reply")
    reply_parser.add_argument("mailfile", type=Path)
    reply_parser.add_argument(
        "--keep-attachments",
        action="store_true",
        default=False,
        help="Keep attachments when replying.",
    )

    reply_all_parser = subparsers.add_parser(
        "reply_all", help="Reply to all recipients of an email."
    )
    reply_all_parser.set_defaults(action="reply_all")
    reply_all_parser.add_argument("mailfile", type=Path)

    update_parser = subparsers.add_parser(
        "update", help="Check for, and install updates."
    )
    update_parser.set_defaults(action="update")

    list_parser = subparsers.add_parser(
        "list", help="List the messages - date, sender, and subject."
    )
    list_parser.set_defaults(action="list")

    remove_parser = subparsers.add_parser(
        "rm",
        help="Delete a message by moving it to the trash. Messages older than 30 days will be permanently deleted.",
    )
    remove_parser.set_defaults(action="remove")
    remove_parser.add_argument(
        "mailnumber",
        help="The message number from the 'list' to delete. Note that message numbers may change when mail is checked, saved, or removed!",
    )

    save_parser = subparsers.add_parser("save", help="Save a message.")
    save_parser.set_defaults(action="save", folder="saved-messages")
    save_parser.add_argument(
        "mailnumber",
        help="Message number to save. Note that message numbers may change when mail is checked, saved, or removed!",
    )
    save_parser.add_argument(
        "--folder",
        default="saved-messages",
        help="Name of the folder to save to. If folder does not exist, it will be created after confirmation.",
    )

    # TODO: It would be pretty cool to have capability to read emails in different viewer, like html open in a browser -W. Werner, 2019-12-06
    read_parser = subparsers.add_parser("read", help="Read a single message")
    read_parser.set_defaults(action="read")
    read_parser.add_argument("mailnumber", type=int)
    read_parser.add_argument(
        "--all-headers", help="Provide all headers instead of a limited set."
    )
    read_parser.add_argument(
        "-p",
        "--part",
        type=int,
        default=None,
        help="Part of the multipart email to read",
    )

    raw_parser = subparsers.add_parser("raw", help="Read a raw/original single message")
    raw_parser.set_defaults(action="raw")
    raw_parser.add_argument("mailnumber", type=int)
    return parser


def action_prompt():
    choice = input("[s]end now, [q]ueue, sa[v]e draft, [d]iscard? ").lower().strip()
    if choice not in ("s", "q", "v", "d"):
        print(f"{choice!r} not a valid input.")
        return action_prompt()
    return choice


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


def commonmarkdown(plain_msg):
    """
    CommonMark-ify the provided msg. Return a multipart email with
    both text and HTML parts.
    """

    if "X-CommonMark" not in plain_msg:
        return plain_msg
    msg = _parser.parsebytes(plain_msg.as_bytes())
    del msg["X-CommonMark"]

    msg.set_content(msg.get_content())
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
    if not related_msg.is_multipart():
        related_msg.make_mixed()
        related_msg.preamble = "This is a MIME-formatted multi-part message."
    attachments = related_msg.get_all("Attachment")

    if attachments is None:
        return msg
    del related_msg["Attachment"]

    attachment_ids = set()
    for attachment in attachments:
        filename, *extra = attachment.split(";")
        filename = Path(filename).expanduser().resolve()
        name = filename.name
        type_, encoding = mimetypes.guess_type(filename.name)
        maintype, _, subtype = (type_ or "application/octet-stream").partition("/")
        disposition = "attachment"
        for bit in extra:
            key, _, val = bit.strip().partition("=")
            key = key.strip()
            val = val.strip()
            if key.lower() == "inline" and val.lower() == "true":
                disposition = "inline"
            elif key.lower() in ("name", "filename"):
                name = ast.literal_eval(val)
        part = EmailMessage(policy=POLICY)
        part.set_content(
            filename.read_bytes(),
            filename=name,
            maintype=maintype,
            subtype=subtype,
            disposition=disposition,
        )
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
    msg = _parser.parsebytes(msg.as_bytes())
    reply = EmailMessage(policy=POLICY)
    reply["From"] = sender

    try:
        from_addr = msg.get("From").addresses[0]
        msg_sender = from_addr.display_name or str(from_addr)
    except AttributeError:
        msg_sender = "Unknown"

    if reply_all:
        to_recipients = getaddresses(msg.get_all("From", []) + msg.get_all("To", []))
        cc_recipients = getaddresses(msg.get_all("Cc", []))
        sender_addr = getaddresses([sender])[0]
        if to_recipients:
            if sender_addr in to_recipients:
                to_recipients.remove(sender_addr)
            reply["To"] = ", ".join(formataddr(addr) for addr in to_recipients)
        if cc_recipients:
            if sender_addr in cc_recipients:
                cc_recipients.remove(sender_addr)
            reply["Cc"] = ", ".join(formataddr(addr) for addr in cc_recipients)
    else:
        for fromaddr in msg.get_all("Reply-To", msg.get_all("From", [])):
            reply["To"] = fromaddr

    reply["Subject"] = "Re: " + msg.get("subject", "")
    date = "a day in the past"
    try:
        date = parsedate_to_datetime(msg["Date"]) or date
    except TypeError:
        date = msg["Date"] or date
    else:
        date = date.strftime("%a, %B %d, %Y at %H:%M:%S%p %z").rstrip()

    try:
        msg_body = msg.get_body(("plain", "html")).get_payload(decode=True).decode()
        reply_body = "> " + msg_body.replace("\n", "\n> ")
    except (KeyError, AttributeError) as e:
        reply_body = "<A message with no text>"

    reply.set_content(f"On {date}, {msg_sender} wrote:\n{reply_body}")

    if keep_attachments and any(msg.iter_attachments()):
        if not reply.is_multipart():
            reply.make_mixed()
        for attachment in msg.iter_attachments():
            reply.attach(attachment)

    return reply


def forwardify(*, msg, sender, keep_attachments=False):
    fwd_msg = EmailMessage(policy=POLICY)

    try:
        date = parsedate_to_datetime(msg["Date"])
    except TypeError:
        date = msg["Date"]
    else:
        date = date.strftime("%a, %B %d, %Y at %H:%M:%S%p %z").rstrip()
    fwd_msg["From"] = sender
    fwd_msg["To"] = ""
    fwd_msg["Subject"] = "Fwd: " + msg.get("Subject", "")
    fwd_msg.set_content(
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
        + msg.get_body(preferencelist=("plain", "html")).get_content()
    )

    # TODO: Should add attachments here, if kept -W. Werner, 2020-03-27
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
                recipients.add(config[addr].get("from", formataddr((name, addr))))
        recipients = list(sorted(recipients))
        if len(recipients) == 1:
            return recipients[0]
        else:
            print("Found multiple possible senders:")
            for i, r in enumerate(recipients, start=1):
                print(f"{i}. {r}")
            done = False
            while not done:
                choice = input(f"Use which address? [1-{len(recipients)}]: ")
                try:
                    recipient = recipients[int(choice) - 1]
                except (IndexError, ValueError):
                    print(f"Invalid choice {choice!r}")
                else:
                    done = True
            return recipient
    else:
        print(msg["To"])
        print(all_recipients)
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
        msg["Date"] = format_datetime(datetime.now(timezone.utc))
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


def _make_draftname(*, subject, timestamp=None):
    timestamp = timestamp or datetime.now()
    sanitized = "-".join(re.sub(r"[^A-Za-z]", " ", subject).split())
    return f"{timestamp:%Y%m%d%H%M%S}-{sanitized}.eml"


def create_draft(*, template, config):
    """
    Creates a draft email from the provided template
    """
    draft_dir = config.get("draft_dir", config["maildir"] / "drafts")
    draft_dir.mkdir(parents=True, exist_ok=True)

    msg = _parser.parsebytes(template.encode())

    f = draft_dir / _make_draftname(subject=(msg["subject"] or ""))
    f.write_text(template)
    return f


def ensure_maildirs_exist(*, maildir):
    maildir = Path(maildir)
    dirnames = ("new", "cur", "drafts", "outbox", "sent")

    for dirname in dirnames:
        (maildir / dirname).mkdir(parents=True, exist_ok=True)


def get_templates(*, dirname):
    path = Path(dirname)
    templates = []
    for f in path.iterdir():
        try:
            templates.append(EmailTemplate(name=f.name, content=f.read_text()))
        except Exception:
            print(f"Failed to read template {f.name}")
    return templates


def reply(*, config, mailfile, reply_all=False, keep_attachments=False):
    if mailfile.name.isdigit():
        curmaildir = config["maildir"] / "cur"
        mailfile = sorted_mailfiles(maildir=curmaildir)[int(mailfile.name) - 1]
    msg = _parser.parsebytes(mailfile.read_bytes())
    msg = replyify(
        msg=msg,
        sender=get_sender(msg=msg, config=config),
        reply_all=reply_all,
        keep_attachments=keep_attachments,
    )
    draft = create_draft(template=msg.as_string(), config=config)
    subprocess.call([config["EDITOR"], draft])
    choice = action_prompt()
    if choice == "s":
        send(config=config, mailfile=draft)


def save(*, config, maildir, mailnumber, target_folder):
    try:
        target_folder = config["maildir"] / target_folder
        mailfile = sorted_mailfiles(maildir=maildir)[abs(int(mailnumber)) - 1]
        with mailfile.open("rb") as f:
            headers = _header_parser.parse(f)
        newfile = target_folder / mailfile.name
        target_folder.mkdir(parents=True, exist_ok=True)
        mailfile.rename(newfile)
        print(
            f'Moved message from {headers["from"]} - {headers["subject"]!r}'
            f" to {target_folder.name}."
        )
    except (FileNotFoundError, IndexError):
        print(f"No mail found with number {mailnumber}")


def remove(*, config, maildir, mailnumber):
    save(
        config=config,
        maildir=maildir,
        mailnumber=mailnumber,
        target_folder=config["maildir"] / "trash",
    )
    # TODO: empty old trash messages -W. Werner, 2020-01-03


def check_email(config):
    maildir = config["maildir"]
    curdir = maildir / "cur"
    newdir = maildir / "new"
    count = 0
    for file in newdir.iterdir():
        count += 1
        nextpath = curdir / file.name
        file.rename(nextpath)
    print(f'{count} new message{"s" if count != 1 else ""}.')


def do_new(config, template_number=None):
    maildir = config["maildir"]
    template_dir = maildir / "templates"
    templates = get_templates(dirname=template_dir)
    if not templates:
        print(f"No templates. Add some to {template_dir} and try again")
        return
    for i, template in enumerate(templates, start=1):
        print(f"{i}. {template.name}")
    done = False
    while not done:
        choice = template_number or input(
            f"Which template? [1-{len(templates)} (^C quits)]: "
        )
        # If cli passed template number, and it was bad, we want to
        # avoid an infinite loop!
        template_number = None
        try:
            template = templates[int(choice) - 1]
        except (IndexError, ValueError):
            print(f"Invalid choice {choice!r}")
        else:
            done = True
    draft = create_draft(template=template.content, config=config)
    subprocess.call([config["EDITOR"], draft])
    choice = action_prompt()
    if choice == "s":
        print("^C to cancel sending")
        timer = config.get("ABORT_TIMEOUT", 5)
        for sec in range(timer):
            try:
                print(f"\rSending in {timer-sec}...[0K", end="")
                sys.stdout.flush()
                time.sleep(1)
            except KeyboardInterrupt:
                print("\r^C caught, draft saved.[0K")
                break
        else:
            print("\rSending now...[0K")
            stage_name = draft.parent.parent / "outbox" / draft.name
            draft.rename(stage_name)
            send(config=config, mailfile=stage_name)
            staged_email_count = len(list((maildir / "outbox").iterdir()))
            if staged_email_count:
                print(
                    f"{staged_email_count} emails to send. Run `{sys.argv[0]} send_all` to send."
                )
    elif choice == "q":
        stage_name = draft.parent.parent / "outbox" / draft.name
        draft.rename(stage_name)
        print(f"Email queued as {stage_name}")
    elif choice == "v":
        with draft.open("rb") as f:
            headers = _header_parser.parse(f)
        subject = subjectify(msg=headers)
        new_name = draft.parent / _make_draftname(subject=subject)
        draft.rename(new_name)
        print(f"Draft saved as {new_name}")
    elif choice == "d":
        choice = input("Really delete draft? Cannot be undone! [y/N]: ")
        if choice.lower() in ("y", "yes", "ja", "si", "oui"):
            draft.unlink()


def send_all(*, config):
    maildir = config["maildir"]
    outbox = maildir / "outbox"
    sentdir = maildir / "sent"
    to_send = list(outbox.iterdir())
    if not to_send:
        print("Nothing to send.")
        return
    print("Going to send...")
    for mailfile in to_send:
        print(mailfile)
    choice = input("Really send all? [Y/n]: ")
    if choice.lower().strip() not in ("", "y", "yes", "si", "oui", "ja"):
        print("Aborted!")
        return
    for mailfile in to_send:
        send(config=config, mailfile=mailfile)
    print("Done!")


def send(*, config, mailfile):
    msg = _parser.parsebytes(mailfile.read_bytes())
    prettyname = f"{prettynow()}-{subjectify(msg=msg)}.eml"
    sentfile = config["maildir"] / "sent" / prettyname
    from_addr = parseaddr(msg["from"])[1]
    config = config.copy()
    if from_addr in config:
        config.update(config[from_addr])
    msg = commonmarkdown(msg)
    msg = attachify(msg)
    mailing_list = msg.get("X-MailingList")
    if mailing_list:
        recipients = [
            r for r in config.get("mailing_list", {}).get(mailing_list) if r.strip()
        ]
        choice = input(f"Sending to {len(recipients)}, continue? [Y/n]: ")
        if choice.lower().strip() in ("n", "no"):
            print("Aborted")
            return
        for recipient in recipients:
            for field in ("to", "cc", "bcc"):
                try:
                    del msg[field]
                except KeyError:
                    pass
            msg["To"] = recipient
            print(f"\tSending to {recipient}...", end="")
            sys.stdout.flush()
            send_message(
                msg=msg,
                smtp_host=config.get("SMTP_HOST", "localhost"),
                smtp_port=config.get("SMTP_PORT", 25),
                use_tls=config.get("SMTP_USE_TLS", False),
                use_smtps=config.get("SMTP_USE_SMTPS", False),
                username=config.get("SMTP_USERNAME", False),
                password=config.get("SMTP_PASSWORD", False),
            )
            print("OK")
    else:
        print(f'Sending {msg["subject"]!r} to {msg["to"]} ... ', end="")
        sys.stdout.flush()
        send_message(
            msg=msg,
            smtp_host=config.get("SMTP_HOST", "localhost"),
            smtp_port=config.get("SMTP_PORT", 25),
            use_tls=config.get("SMTP_USE_TLS", False),
            use_smtps=config.get("SMTP_USE_SMTPS", False),
            username=config.get("SMTP_USERNAME", False),
            password=config.get("SMTP_PASSWORD", False),
        )
        print("OK")
    sentfile.parent.mkdir(parents=True, exist_ok=True)
    mailfile.rename(sentfile)


def get_msg_date(file):
    file = Path(file)
    with file.open("rb") as f:
        headers = _header_parser.parse(f)
    msg_timestamp = datetime.fromtimestamp(file.stat().st_mtime, LOCAL_TZ)
    if headers["date"]:
        msg_timestamp = parsedate_to_datetime(headers["date"])
        if msg_timestamp.tzinfo is None:
            msg_timestamp = msg_timestamp.replace(tzinfo=timezone.utc)
    return msg_timestamp


def sorted_mailfiles(*, maildir):
    msg_list = [file for file in maildir.iterdir() if file.is_file()]
    msg_list.sort(key=get_msg_date)
    return msg_list


def iter_headers(*, maildir):
    for file in sorted_mailfiles(maildir=maildir):
        with file.open("rb") as f:
            headers = _header_parser.parse(f)
        yield headers


def iter_messages(*, maildir):
    """
    Iterate over the messages in maildir, yielding each parsed message.
    """
    for file in sorted_mailfiles(maildir=maildir):
        msg = _parser.parsebytes(file.read_bytes())
        yield msg


def list_messages(*, config):
    maildir = config["maildir"] / "cur"
    for i, msg in enumerate(iter_headers(maildir=maildir), start=1):
        if "date" in msg:
            date = parsedate_to_datetime(msg["date"])
            date_str = f"{date:%Y-%m-%d %H:%M}"
        else:
            date_str = f"{'Unknown':<16}"
        subject = msg["subject"]
        # TODO: There are a number of headers this could be -W. Werner, 2019-11-22
        sender = msg["from"] or msg["sender"]
        print(f"{i:>2}. {date_str} - {sender} - {subject}")


def raw(*, config, mailnumber):
    mailfile = sorted_mailfiles(maildir=config["maildir"] / "cur")[mailnumber - 1]
    subprocess.run([config["EDITOR"], mailfile.resolve()])


def read(*, config, mailnumber, all_headers=False, part=None):
    message_iter = iter_messages(maildir=config["maildir"] / "cur")
    # TODO: This works but it doesn't have comprehensive test coverage -W. Werner, 2019-12-06
    # Also there is another issue. If there is a part with a filename, we should try and respect that filename. This should kind of get unwound. Also it's not super effective
    # to go parsing all of the emails *shrugs*
    for i, msg in zip(range(mailnumber), message_iter):
        pass
    with tempfile.NamedTemporaryFile(suffix=".eml") as tempmail:
        if all_headers:
            tempmail.write(msg.as_bytes().split(b"\n\n")[0])
        else:
            for header in DISPLAY_HEADERS:
                if header in msg:
                    tempmail.write(f"{header}: {msg[header]}\n".encode())

        tempmail.write(b"\n\n")

        if not msg.is_multipart():
            tempmail.write(msg.get_payload(decode=True))
        else:
            parts = []
            i = 1
            for msgpart in msg.walk():
                content_type = msgpart.get_content_type()
                if content_type.startswith("multipart/"):
                    print(content_type)
                else:
                    parts.append(msgpart)
                    print(f"\t{i}. {content_type}")
                    i += 1
            msgpart = parts[
                (part or config.get("default_part") or int(input("What part? "))) - 1
            ]
            tempmail.write(msgpart.get_payload(decode=True))
        tempmail.flush()
        subprocess.run([config["EDITOR"], tempmail.name])


def filter_messages(*, config, folder=None):
    folder = config["maildir"] / (folder or "cur")
    for filter in (f for f in config.get("filters", []) if f):
        ret = subprocess.run(filter + [str(folder)], capture_output=True)
        if ret.returncode:
            break


def update():
    ...


def load_config(config_file):
    config = json.load(config_file)
    if "" in config:
        config.pop("")

    config["maildir"] = (
        Path(config.get("maildir", config.get("MAILDIR", "~/wemail/")))
        .expanduser()
        .resolve()
    )
    config["EDITOR"] = config.get(
        "EDITOR", os.environ.get("EDITOR", os.environ.get("VISUAL", "nano"))
    )
    return config


def do_it_two_it(args):  # Shia LeBeouf!
    if args.version:
        print(__version__)
        return
    try:
        config = load_config(args.config)
        ensure_maildirs_exist(maildir=config["maildir"])
        if args.action == "new":
            return do_new(config=config, template_number=args.template_number)
        elif args.action == "send":
            return send(config=config, mailfile=args.mailfile)
        elif args.action == "send_all":
            return send_all(config=config)
        elif args.action == "check":
            return check_email(config=config)
        elif args.action == "reply":
            return reply(
                config=config,
                mailfile=args.mailfile,
                keep_attachments=args.keep_attachments,
            )
        elif args.action == "reply_all":
            return reply(config=config, mailfile=args.mailfile, reply_all=True)
        elif args.action == "filter":
            return filter_messages(config=config, folder=args.folder)
        elif args.action == "update":
            return update()
        elif args.action == "list":
            return list_messages(config=config)
        elif args.action == "read":
            return read(
                config=config,
                mailnumber=args.mailnumber,
                all_headers=args.all_headers,
                part=args.part,
            )
        elif args.action == "raw":
            return raw(config=config, mailnumber=args.mailnumber)
        elif args.action == "save":
            return save(
                config=config,
                maildir=config["maildir"] / "cur",
                mailnumber=args.mailnumber,
                target_folder=args.folder,
            )
        elif args.action == "remove":
            return remove(
                config=config,
                maildir=config["maildir"] / "cur",
                mailnumber=args.mailnumber,
            )

    except KeyboardInterrupt:
        print("\n^C caught, bye!")


def do_it_now(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)
    if not args.version and "action" not in args:
        parser.exit(message=parser.format_help())
    do_it_two_it(args)


if __name__ == "__main__":
    do_it_now()
