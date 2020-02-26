import io
import datetime
import json
import pathlib
import ssl
import pkg_resources
import smtplib
import tempfile
import textwrap

import pytest
import mistletoe

import wemail
from email.mime.application import MIMEApplication
from email.utils import getaddresses
from unittest import mock

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP as SMTPProtocol, syntax


class MyHandler:
    def __init__(self):
        self.box = []

    async def handle_DATA(self, server, session, envelope):
        self.box.append(envelope)
        return "250 OK"

    async def handle_AUTH(self, server, session, envelope, protocol, credentials):
        return "235 2.7.0  Authentication Suceeded, woo woo"

    async def handle_EHLO(self, server, session, envelope, hostname):
        session.host_name = hostname
        return "250-AUTH PLAIN\n250-STARTTLS\n250 HELP"


parser = wemail.make_parser()


# {{{ Fixtures


@pytest.fixture()
def sample_good_mailfile():
    with tempfile.TemporaryDirectory() as dirname:
        mailfile = pathlib.Path(dirname) / "draft" / "test.eml"
        mailfile.parent.mkdir(parents=True, exist_ok=True)
        mailfile.write_text(
            textwrap.dedent(
                """
            From: Person Man <person@example.com>
            To: Triangle Man <triangle@example.com>
            Subject: why don't you like me?

            I mean... you clearly don't - so why not?
            """
            ).strip()
        )
        yield mailfile


@pytest.fixture()
def test_server():
    try:
        handler = MyHandler()
        controller = Controller(handler, port=8173)
        controller.handler = handler
        controller.start()
        yield controller
    finally:
        controller.stop()


@pytest.fixture()
def ssl_test_server():
    try:
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(
            pkg_resources.resource_filename("util", "server.crt"),
            pkg_resources.resource_filename("util", "server.key"),
        )

        class Proto(SMTPProtocol):
            @syntax("AUTH protocol data")
            async def smtp_AUTH(self, arg):
                if not arg:
                    await self.push("250 AUTH PLAIN")
                else:
                    await self.push("235 2.7.0  Authentication Suceeded, woo woo")

        handler = MyHandler()

        class TLSController(Controller):
            def factory(self):
                return Proto(
                    self.handler,
                    decode_data=False,
                    require_starttls=False,
                    tls_context=ssl_context,
                )

        controller = TLSController(handler, port=8874)
        controller.handler = handler
        controller.start()
        yield controller
    finally:
        controller.stop()


@pytest.fixture(scope="module")
def goodconfig():
    with tempfile.TemporaryDirectory() as dirname:
        config = {
            "draftdir": dirname,
            "default_headers": {
                "From": "Wayne Werner <wayne@waynewerner.com>",
                "To": "",
                "X-CommonMark": True,
                "Subject": "",
            },
        }
        yield config


@pytest.fixture()
def goodheaders():
    headers = {
        "From": "roscivs@indessed.example.com",
        "To": "wayne@example.com",
        "Subject": "testing",
    }
    return headers


@pytest.fixture()
def temp_maildir():
    with tempfile.TemporaryDirectory() as dirname:
        dirp = pathlib.Path(dirname)
        wemail.ensure_maildirs_exist(maildir=dirp)
        newmail_dir = dirp / "new"
        for i in range(1, 4):
            fname = newmail_dir / f"message{i}.eml"
            if i == 3:
                date_header = "Date: Mon, 14 Aug 2010 13:32:02 +0100\n"
            else:
                date_header = ""
            fname.write_text(
                f"""{date_header}From: person.man@example.com\nTo: triangle.man@example.com\nSubject: I hate you\n\nLet's have a fight!"""
            )
        yield dirname


@pytest.fixture()
def good_draft(goodheaders):
    msg = wemail.EmailMessage()
    for header in goodheaders:
        msg[header] = goodheaders[header]
    yield msg


@pytest.fixture()
def good_attachment_email(good_draft):
    good_draft.add_attachment("This is some text")
    yield good_draft


@pytest.fixture()
def good_config(temp_maildir):
    file = io.StringIO()
    json.dump(
        {
            "maildir": temp_maildir,
            "EDITOR": "echo",
            "roscivs@indessed.example.com": {
                "from": "Roscivs Bottia <roscivs@indessed.example.com>"
            },
            "no.from@example.com": {},
        },
        file,
    )
    file.seek(0)
    return file


@pytest.fixture()
def good_loaded_config(good_config):
    config = wemail.load_config(good_config)
    return config


@pytest.fixture()
def args_new_alone(good_config):
    return Namespace(action="new", config=good_config, version=False)


@pytest.fixture()
def args_new_alone(good_config):
    args = parser.parse_args(["new"])
    args.config = good_config
    return args


@pytest.fixture()
def args_send(good_config):
    with tempfile.NamedTemporaryFile() as f:
        args = parser.parse_args(["send", f.name])
        yield args


@pytest.fixture()
def args_list(good_config):
    args = parser.parse_args(["list"])
    args.config = good_config
    return args


@pytest.fixture()
def args_read(good_config):
    args = parser.parse_args(["read", "1"])
    args.config = good_config
    return args


@pytest.fixture()
def args_send_all(good_config):
    args = parser.parse_args(["send_all"])
    return args


@pytest.fixture()
def args_check(good_config):
    args = parser.parse_args(["check"])
    return args


@pytest.fixture()
def args_reply(good_config):
    with tempfile.NamedTemporaryFile() as f:
        args = parser.parse_args(["reply", f.name])
        yield args


@pytest.fixture()
def args_reply_keep_attach(good_config):
    with tempfile.NamedTemporaryFile() as f:
        args = parser.parse_args(["reply", f.name, "--keep-attachments"])
        yield args


@pytest.fixture()
def args_reply_all(good_config):
    with tempfile.NamedTemporaryFile() as f:
        args = parser.parse_args(["reply_all", f.name])
        yield args


@pytest.fixture()
def args_filter(good_config):
    args = parser.parse_args(["filter"])
    return args


@pytest.fixture()
def args_update(good_config):
    args = parser.parse_args(["update"])
    return args


@pytest.fixture()
def args_version(good_config):
    args = parser.parse_args(["--version"])
    return args


@pytest.fixture()
def args_save():
    args = parser.parse_args(["save", "1", "--folder", "saved-messages"])
    return args


@pytest.fixture()
def args_remove():
    args = parser.parse_args(["rm", "1"])
    return args


@pytest.fixture()
def args_bad_rm_number():
    args = parser.parse_args(["rm", "6"])
    return args


@pytest.fixture()
def args_bad_rm_number():
    args = parser.parse_args(["rm", "6"])
    return args


# end fixtures }}}

# {{{ action tests


def test_when_action_is_new_it_should_do_new(args_new_alone, good_loaded_config):
    patch_config = mock.patch("wemail.load_config", return_value=good_loaded_config)
    with mock.patch("wemail.do_new", autospec=True) as fake_do_new, patch_config:
        wemail.do_it_two_it(args_new_alone)
        fake_do_new.assert_called_with(config=good_loaded_config)


def test_when_action_is_send_it_should_send(args_send, good_loaded_config):
    patch_send = mock.patch("wemail.send", autospec=True)
    patch_config = mock.patch("wemail.load_config", return_value=good_loaded_config)
    with patch_send as fake_send, patch_config:
        wemail.do_it_two_it(args_send)
        fake_send.assert_called_with(
            config=good_loaded_config, mailfile=args_send.mailfile
        )


def test_when_action_is_send_all_it_should_send_all(args_send_all, good_loaded_config):
    patch_send_all = mock.patch("wemail.send_all", autospec=True)
    patch_config = mock.patch("wemail.load_config", return_value=good_loaded_config)
    with patch_send_all as fake_send_all, patch_config:
        wemail.do_it_two_it(args_send_all)
        fake_send_all.assert_called_with(config=good_loaded_config)


def test_when_action_is_check_it_should_check(args_check, good_loaded_config):
    patch_check_email = mock.patch("wemail.check_email", autospec=True)
    patch_config = mock.patch("wemail.load_config", return_value=good_loaded_config)
    with patch_check_email as fake_check, patch_config:
        wemail.do_it_two_it(args_check)
        fake_check.assert_called_with(config=good_loaded_config)


def test_when_action_is_reply_it_should_reply(args_reply, good_loaded_config):
    patch_reply = mock.patch("wemail.reply", autospec=True)
    patch_config = mock.patch("wemail.load_config", return_value=good_loaded_config)
    with patch_reply as fake_reply, patch_config:
        wemail.do_it_two_it(args_reply)
        fake_reply.assert_called_with(
            config=good_loaded_config,
            mailfile=args_reply.mailfile,
            keep_attachments=False,
        )


def test_when_action_is_reply_and_keep_attachment_it_should_be_set(
    args_reply_keep_attach, good_loaded_config
):
    patch_reply = mock.patch("wemail.reply", autospec=True)
    patch_config = mock.patch("wemail.load_config", return_value=good_loaded_config)
    with patch_reply as fake_reply, patch_config:
        wemail.do_it_two_it(args_reply_keep_attach)
        fake_reply.assert_called_with(
            config=good_loaded_config,
            mailfile=args_reply_keep_attach.mailfile,
            keep_attachments=True,
        )


def test_when_action_is_reply_all_it_should_reply_all(
    args_reply_all, good_loaded_config
):
    patch_reply_all = mock.patch("wemail.reply", autospec=True)
    patch_config = mock.patch("wemail.load_config", return_value=good_loaded_config)
    with patch_reply_all as fake_reply_all, patch_config:
        wemail.do_it_two_it(args_reply_all)
        fake_reply_all.assert_called_with(
            config=good_loaded_config, mailfile=args_reply_all.mailfile, reply_all=True
        )


def test_when_action_is_filter_it_should_filter(args_filter, good_loaded_config):
    patch_filter = mock.patch("wemail.filter_messages", autospec=True)
    patch_config = mock.patch("wemail.load_config", return_value=good_loaded_config)
    with patch_filter as fake_filter, patch_config:
        wemail.do_it_two_it(args_filter)
        fake_filter.assert_called_with(
            config=good_loaded_config, folder=args_filter.folder
        )


def test_when_action_is_update_it_should_update(args_update):
    patch_update = mock.patch("wemail.update", autospec=True)
    with patch_update as fake_update:
        wemail.do_it_two_it(args_update)
        fake_update.assert_called_with()


def test_when_action_is_list_it_should_list(args_list, good_loaded_config):
    patch_config = mock.patch("wemail.load_config", return_value=good_loaded_config)
    patch_list = mock.patch("wemail.list_messages", autospec=True)
    with patch_list as fake_list, patch_config:
        wemail.do_it_two_it(args_list)
        fake_list.assert_called_with(config=good_loaded_config)


def test_when_action_is_save_it_should_save(args_save, good_loaded_config):
    patch_config = mock.patch("wemail.load_config", return_value=good_loaded_config)
    patch_save = mock.patch("wemail.save", autospec=True)
    with patch_save as fake_save, patch_config:
        wemail.do_it_two_it(args_save)
        fake_save.assert_called_with(
            config=good_loaded_config,
            maildir=good_loaded_config["maildir"] / "cur",
            mailnumber=args_save.mailnumber,
            target_folder=args_save.folder,
        )


def test_when_action_is_read_it_should_read(args_read, good_loaded_config):
    patch_config = mock.patch("wemail.load_config", return_value=good_loaded_config)
    patch_read = mock.patch("wemail.read", autospec=True)
    with patch_read as fake_read, patch_config:
        wemail.do_it_two_it(args_read)
        fake_read.assert_called_with(
            config=good_loaded_config,
            mailnumber=args_read.mailnumber,
            all_headers=args_read.all_headers,
        )


def test_when_good_remove_is_passed_it_should_remove(args_remove, good_loaded_config):
    patch_config = mock.patch("wemail.load_config", return_value=good_loaded_config)
    patch_remove = mock.patch("wemail.remove", autospec=True)
    with patch_remove as fake_remove, patch_config:
        wemail.do_it_two_it(args_remove)
        fake_remove.assert_called_with(
            config=good_loaded_config,
            maildir=good_loaded_config["maildir"] / "cur",
            mailnumber=args_remove.mailnumber,
        )


def test_when_version_is_passed_it_should_display_version(
    capsys, args_version, good_loaded_config
):
    wemail.do_it_two_it(args_version)
    captured = capsys.readouterr()
    assert captured.out == f"{wemail.__version__}\n"


def test_when_no_action_is_on_args_it_should_get_help():
    with pytest.raises(SystemExit):
        wemail.do_it_now([])


# end action test }}}


######################
# {{{ Template Tests #
######################


def test_get_templates_with_empty_dir_should_return_no_templates():
    no_templates = []
    with tempfile.TemporaryDirectory() as dirname:
        actual_templates = wemail.get_templates(dirname=dirname)

        assert actual_templates == no_templates


# I don't know for sure if we want to use the filename as the template
# name, but at least for now it works well. - W. Werner
def test_get_templates_with_one_template_should_return_that_template():
    template = textwrap.dedent(
        """
        From: wayne@example.com
        To:
        X=CommonMark: True
        Subject:

        """
    )
    expected_name = "wayne@example.com"
    expected_templates = [template]
    with tempfile.TemporaryDirectory() as dirname:
        p = pathlib.Path(dirname) / expected_name
        p.write_text(template)

        actual_templates = wemail.get_templates(dirname=dirname)

        assert len(actual_templates) == 1
        assert actual_templates[0].name == expected_name
        assert actual_templates[0].content == template


def test_get_templates_with_multiple_templates_should_return_templates():
    expected_templates = [
        wemail.EmailTemplate(
            name="blarp okay?",
            content=textwrap.dedent(
                """
                From: person@wherever.example.com
                To: me@here.example.net

                """
            ).strip(),  # Yeah, only stripping this one. It shouldn't matter.
        ),
        wemail.EmailTemplate(
            name="boop be doop",
            content=textwrap.dedent(
                """
                From: person@not.example.com
                """
            ),
        ),
    ]
    expected_templates.sort()

    with tempfile.TemporaryDirectory() as dirname:
        for t in expected_templates:
            p = pathlib.Path(dirname) / t.name
            p.write_text(t.content)

        actual_templates = wemail.get_templates(dirname=dirname)
        actual_templates.sort()

        assert actual_templates == expected_templates


########################
# End Template Tests }}}
########################


############################
# {{{ Create Draft Tests #
############################


def test_create_drafts_should_create_a_draft_from_the_email():
    template = textwrap.dedent(
        """
        From: whatever@example.com
        To: person_man@example.com
        Subject: Testing 123
        """
    ).strip()

    with tempfile.TemporaryDirectory() as dirname:
        maildir = pathlib.Path(dirname)
        drafts = maildir / "drafts"
        config = {"maildir": maildir}
        wemail.create_draft(template=template, config=config)

        files = list(drafts.iterdir())
        assert len(files) == 1
        assert files[0].read_text() == template


def test_create_drafts_should_use_created_filename():
    # TODO: DRY this out with the other one -W. Werner, 2019-11-17
    template = textwrap.dedent(
        """
        From: whatever@example.com
        To: person_man@example.com
        Subject: Testing 123
        """
    ).strip()
    expected_name = "blargle.eml"

    with tempfile.TemporaryDirectory() as dirname:
        maildir = pathlib.Path(dirname)
        drafts = maildir / "drafts"
        config = {"maildir": maildir}
        fake_draftname = mock.MagicMock(return_value=expected_name)
        with mock.patch("wemail._make_draftname", fake_draftname):
            wemail.create_draft(template=template, config=config)

        assert (drafts / expected_name).read_text() == template


def test_make_draftname_should_be_combination_of_the_current_timestamp_and_subject():
    subject = "Something Cool"
    expected_draftname = "20100824000000-Something-Cool.eml"
    actual_draftname = wemail._make_draftname(
        subject=subject, timestamp=datetime.datetime(2010, 8, 24, 0, 0, 0)
    )

    assert actual_draftname == expected_draftname


##########################
# End Create Draft Tests }}}
##########################


########################
# {{{ Maildir tests #
########################


def test_ensure_maildirs_exist_should_create_proper_dirs():
    with tempfile.TemporaryDirectory() as dirname:
        maildir = pathlib.Path(dirname)
        wemail.ensure_maildirs_exist(maildir=maildir)

        assert (maildir / "cur").exists()
        assert (maildir / "new").exists()
        assert (maildir / "drafts").exists()
        assert (maildir / "outbox").exists()
        assert (maildir / "sent").exists()


def test_files_should_be_sorted_in_date_header_order():
    with tempfile.TemporaryDirectory() as dirname:
        maildir = pathlib.Path(dirname)
        wemail.ensure_maildirs_exist(maildir=maildir)
        dates = (
            "Date: Mon, 14 Aug 2010 13:32:02 +0100\nSubject: Second\n\n",
            "Date: Mon, 14 Aug 2011 13:32:02 +0100\nSubject: Third\n\n",
            "Date: Mon, 26 Aug 1984 13:32:02 +0100\nSubject: First\n\n",
        )
        expected_order = ("First", "Second", "Third")

        for i, date in enumerate(dates):
            filename = maildir / "new" / f"file_{i}.eml"
            filename.write_text(date)

        actual_order = tuple(
            msg["subject"] for msg in wemail.iter_messages(maildir=maildir / "new")
        )

        assert actual_order == expected_order


#####################
# End maildir tests }}}
#####################


# {{{ Check email tests
######################


def test_when_check_email_is_called_it_should_print_the_number_of_new_emails(
    capsys, good_loaded_config
):
    wemail.check_email(config=good_loaded_config)

    captured = capsys.readouterr()
    assert captured.out == "3 new messages.\n"

    fname = good_loaded_config["maildir"] / "new" / f"message.eml"
    fname.write_text("From: me\nTo: you\nSubject: OK\n\nOK?")

    wemail.check_email(config=good_loaded_config)

    captured = capsys.readouterr()
    assert captured.out == "1 new message.\n"

    for i in range(10):
        fname = good_loaded_config["maildir"] / "new" / f"message{i}.eml"
        fname.write_text("From: me\nTo: you\nSubject: OK\n\nOK?")

    wemail.check_email(config=good_loaded_config)

    captured = capsys.readouterr()
    assert captured.out == "10 new messages.\n"


def test_when_check_email_is_called_it_should_move_all_files_from_new_to_cur(
    good_loaded_config,
):
    maildir = good_loaded_config["maildir"]
    for i in range(5):
        fname = maildir / "new" / f"message{i}.eml"
        fname.write_text("From: me\nTo: you\nSubject: OK\n\nOK?")
    expected_files = [file.name for file in (maildir / "new").iterdir()]
    expected_files.sort()

    wemail.check_email(config=good_loaded_config)

    actual_files = [file.name for file in (maildir / "cur").iterdir()]
    actual_files.sort()

    assert expected_files == actual_files
    assert expected_files != []


# End Check email tests }}}

# {{{ Read email tests


def test_simple_single_mail_should_fire_external_viewer_with_email(good_loaded_config):
    wemail.check_email(good_loaded_config)
    msg = next(wemail.iter_messages(maildir=good_loaded_config["maildir"] / "cur"))
    with mock.patch("subprocess.run", autospec=True) as fake_run:
        wemail.read(config=good_loaded_config, mailnumber=1)
        fake_run.assert_called_with([good_loaded_config["EDITOR"], mock.ANY])


def test_list_should_list_the_messages(capsys, good_loaded_config):
    expected_message = (
        " 1. 2010-08-14 13:32 - person.man@example.com - I hate you\n"
        " 2. Unknown          - person.man@example.com - I hate you\n"
        " 3. Unknown          - person.man@example.com - I hate you\n"
    )
    wemail.check_email(good_loaded_config)
    capsys.readouterr()

    wemail.list_messages(config=good_loaded_config)
    captured = capsys.readouterr()

    assert captured.out == expected_message


# End read email tests }}}

# {{{ Send email tests


def test_send_email_should_send_provided_email(sample_good_mailfile, test_server):
    config = {
        "SMTP_HOST": test_server.hostname,
        "SMTP_PORT": test_server.port,
        "maildir": sample_good_mailfile.parent.parent,
    }

    expected_message = wemail._parser.parsebytes(sample_good_mailfile.read_bytes())
    wemail.send(config=config, mailfile=sample_good_mailfile)

    actual_message = wemail._parser.parsebytes(test_server.handler.box[0].content)

    for header in ("from", "to", "subject"):
        assert actual_message[header] == expected_message[header]

    assert actual_message.get_payload() == expected_message.get_payload()


def test_send_email_with_use_ssl_should_send_provided_email(
    sample_good_mailfile, ssl_test_server
):
    config = {
        "SMTP_HOST": ssl_test_server.hostname,
        "SMTP_PORT": ssl_test_server.port,
        "SMTP_USERNAME": "fnord",
        "SMTP_PASSWORD": "fnord",
        "SMTP_USE_TLS": True,
        "maildir": sample_good_mailfile.parent.parent,
    }

    expected_message = wemail._parser.parsebytes(sample_good_mailfile.read_bytes())
    wemail.send(config=config, mailfile=sample_good_mailfile)

    actual_message = wemail._parser.parsebytes(ssl_test_server.handler.box[0].content)

    for header in ("from", "to", "subject"):
        assert actual_message[header] == expected_message[header]

    assert actual_message.get_payload() == expected_message.get_payload()


def test_send_should_display_sending_status(capsys, sample_good_mailfile):
    expected_message = 'Sending "why don\'t you like me?" to Triangle Man <triangle@example.com> ... OK\n'

    with mock.patch("wemail.send_message", autospec=True):
        wemail.send(
            config={"maildir": sample_good_mailfile.parent.parent},
            mailfile=sample_good_mailfile,
        )

    captured = capsys.readouterr()
    assert captured.out == expected_message


def test_send_should_override_defaults_with_account_settings_from_config(
    sample_good_mailfile,
):
    expected_host = "goodhost"
    expected_port = 0xC00D
    expected_use_tls = True
    expected_use_smtps = True
    expected_username = "goodboy"
    expected_password = "CorrectHorseBatteryStaple"
    config = {
        "maildir": sample_good_mailfile.parent.parent,
        "SMTP_HOST": "bad bad bad",
        "SMTP_PORT": 0xBADBAD,
        "SMTP_USE_TLS": "Bad no good override",
        "SMTP_USE_SMTPS": "Bad no good override",
        "SMTP_USERNAME": "Bad no good override",
        "SMTP_PASSWORD": "Bad no good override",
        "person@example.com": {
            "SMTP_HOST": expected_host,
            "SMTP_PORT": expected_port,
            "SMTP_USE_TLS": expected_use_tls,
            "SMTP_USE_SMTPS": expected_use_smtps,
            "SMTP_USERNAME": expected_username,
            "SMTP_PASSWORD": expected_password,
        },
    }
    email = wemail._parser.parsebytes(sample_good_mailfile.read_bytes())
    mock_parser = mock.patch(
        "wemail._parser.parsebytes", mock.MagicMock(return_value=email)
    )
    with mock.patch(
        "wemail.send_message", autospec=True
    ) as fake_send_message, mock_parser:
        wemail.send(config=config, mailfile=sample_good_mailfile)

        fake_send_message.assert_called_with(
            msg=email,
            smtp_host=expected_host,
            smtp_port=expected_port,
            use_tls=expected_use_tls,
            use_smtps=expected_use_smtps,
            username=expected_username,
            password=expected_password,
        )


def test_send_should_move_mailfile_to_sent_after_success(sample_good_mailfile):
    sent_dir = sample_good_mailfile.parent.parent / "sent"
    expected_text = sample_good_mailfile.read_text()

    with mock.patch("wemail.send_message", autospec=True):
        wemail.send(
            config={"maildir": sample_good_mailfile.parent.parent},
            mailfile=sample_good_mailfile,
        )

    sent_files = list(sent_dir.iterdir())
    assert len(sent_files) == 1, sent_files
    actual_text = sent_files[0].read_text()
    assert actual_text == expected_text


# End send meessages }}}

# {{{ Reply email tests


def test_reply_email_should_send_when_done_composing_if_told(good_loaded_config):
    wemail.check_email(good_loaded_config)

    mailfile = wemail.sorted_mailfiles(maildir=good_loaded_config["maildir"] / "cur")[0]
    good_loaded_config["EDITOR"] = "tail"

    mock_send = mock.patch("wemail.send", autospec=True)
    mock_draftname = mock.patch("wemail._make_draftname", return_value=mailfile.name)
    mock_draftdir = mock.patch.dict(good_loaded_config, {"draft_dir": mailfile.parent})

    with mock.patch(
        "builtins.input", return_value="s"
    ), mock_send as fake_send, mock_draftname, mock_draftdir:
        wemail.reply(config=good_loaded_config, mailfile=mailfile)

        fake_send.assert_called_with(config=good_loaded_config, mailfile=mailfile)


def test_reply_should_keep_attachment_if_told(
    good_attachment_email, good_loaded_config
):
    mailfile = good_loaded_config["maildir"] / "cur" / "fnord.eml"
    mailfile.write_bytes(good_attachment_email.as_bytes())
    mock_send = mock.patch("wemail.send", autospec=True)
    with mock.patch("builtins.input", return_value="s"), mock_send as fake_send:
        wemail.reply(
            config=good_loaded_config, mailfile=mailfile, keep_attachments=True
        )

        args, kwargs = fake_send.call_args
        msg = kwargs["mailfile"]
        email = wemail._parser.parsebytes(msg.read_bytes())
        print(email)

        attachment = next(email.iter_attachments())
        expected_attachment = next(good_attachment_email.iter_attachments())

        assert attachment.get_content() == expected_attachment.get_content()


def test_reply_email_should_remove_quoted_printable_encoding(good_loaded_config):
    mailfile = good_loaded_config["maildir"] / "cur" / "fnord.eml"
    mailfile.write_text(
        textwrap.dedent(
            """\
        From: Person Man <person@example.com>
        To: Triangle Man <triangle@example.com>
        Content-Transfer-Encoding: quoted-printable
        Subject: why don't you like me?

        This=20is=20my=20message=20=F0=9F=90=8D
        """
        ).strip()
    )
    mock_send = mock.patch("wemail.send", autospec=True)
    with mock.patch("builtins.input", return_value="s"), mock_send as fake_send:
        wemail.reply(config=good_loaded_config, mailfile=mailfile)

        args, kwargs = fake_send.call_args
        msg = kwargs["mailfile"]
        email = wemail._parser.parsebytes(msg.read_bytes())
        encodings = email.get_all("content-transfer-encoding")
        assert "quoted-printable" not in encodings, encodings
        assert "> This is my message \N{SNAKE}" in email.get_content()


def test_reply_with_message_that_has_no_plain_text_should_add_it(good_loaded_config):
    mailfile = good_loaded_config["maildir"] / "cur" / "noplaintext.eml"
    mailfile.write_text(
        textwrap.dedent(
            """\
        From: Person Man <person@example.com>
        To: Triangle Man <triangle@example.com>
        MIME-Version: 1.0
        Content-Type: multipart/related; boundary="===============0642271926=="

        --===============0642271926==
        Content-Type: application/json; charset="utf-8"
        Content-Transfer-Encoding: 7bit

        {"foo": "bar"}

        --===============0642271926==--
        """
        )
    )
    mock_send = mock.patch("wemail.send", autospec=True)
    with mock.patch("builtins.input", return_value="s"), mock_send as fake_send:
        wemail.reply(config=good_loaded_config, mailfile=mailfile)

        args, kwargs = fake_send.call_args
        msg = kwargs["mailfile"]
        email = wemail._parser.parsebytes(msg.read_bytes())

        body = email.get_body(("plain",))
        assert (
            body.get_content()
            == "On a day in the past, Person Man wrote:\n<A message with no text>\n"
        )


def test_replyify_with_no_sender_should_set_unknown_to_sender():
    msg = wemail.replyify(
        msg=wemail._parser.parsebytes(
            textwrap.dedent(
                """\
        To: someone@example.com
        Subject: Awesome

        Nobody sent this.
    """
            ).encode()
        ),
        sender="whatever@example.com",
    )

    assert "Unknown wrote" in msg.get_content()


def test_replify_with_reply_all_should_to_the_sender_and_cc_other_recipients(
    good_draft
):
    good_draft[
        "Cc"
    ] = f"test1@example.com, test2@example.com, fnord@example.com, {good_draft['to']}"

    response = wemail.replyify(msg=good_draft, sender=good_draft["to"], reply_all=True)

    to_recipients = wemail.getaddresses(response.get_all("to", []))
    cc_recipients = wemail.getaddresses(response.get_all("cc", []))

    assert to_recipients == [("", "roscivs@indessed.example.com")]
    assert cc_recipients == [
        ("", "test1@example.com"),
        ("", "test2@example.com"),
        ("", "fnord@example.com"),
    ]


# End reply email tests }}}

# {{{ Custom header tests


def test_when_commonmark_header_is_present_it_should_render_message(
    sample_good_mailfile,
):
    sample_good_mailfile.write_text(
        textwrap.dedent(
            """
            From: test@example.com
            To: test@example.com
            X-CommonMark: True
            Subject: Testing

            *bold* text
            """
        ).strip()
    )
    message = wemail._parser.parsebytes(sample_good_mailfile.read_bytes())
    patch_send_message = mock.patch("wemail.send_message")

    with mock.patch(
        "wemail.commonmarkdown", autospec=True, return_value=message
    ) as fakemarkdown, patch_send_message as fake_sm:
        wemail.send(
            config={"maildir": sample_good_mailfile.parent.parent},
            mailfile=sample_good_mailfile,
        )
        fakemarkdown.assert_called()


# End custom header tests }}}

# {{{ Remove tests


@pytest.mark.parametrize("mailnumber", [6, 10, 42])
def test_when_rm_is_called_on_non_existent_message_it_should_display_no_mail_message(
    capsys, good_loaded_config, args_bad_rm_number, mailnumber
):
    args_bad_rm_number.mailnumber = mailnumber
    expected_message = f"No mail found with number {mailnumber}\n"

    curmaildir = good_loaded_config["maildir"] / "cur"
    assert sum(1 for _ in curmaildir.iterdir()) < 5

    patch_config = mock.patch("wemail.load_config", return_value=good_loaded_config)
    with patch_config:
        wemail.do_it_two_it(args_bad_rm_number)

    captured = capsys.readouterr()
    assert captured.out == expected_message


def test_when_rm_is_called_on_message_that_exists_it_should_go_away(
    good_loaded_config, temp_maildir
):
    temp_maildir = pathlib.Path(temp_maildir) / "new"
    count = 0
    for file in wemail.sorted_mailfiles(maildir=temp_maildir):
        count += 1
        assert file.exists(), "Mailfile somehow got pre-deleted?"
        wemail.remove(config=good_loaded_config, maildir=temp_maildir, mailnumber=1)
        assert not file.exists(), "Mailfile failed to delete"
    assert count > 0, "We did not actually check deleting any files"


def test_when_rm_is_called_it_should_print_which_message_was_removed(
    capsys, good_loaded_config, temp_maildir
):
    temp_maildir = pathlib.Path(temp_maildir) / "new"
    expected_message = (
        "Moved message from person.man@example.com - 'I hate you' to trash.\n"
    )

    wemail.remove(config=good_loaded_config, maildir=temp_maildir, mailnumber=1)

    captured = capsys.readouterr()
    assert captured.out == expected_message


# End remove tests }}}

# {{{ Save email tests


@pytest.mark.parametrize("mailnumber", [6, 10, 42])
def test_when_save_is_called_on_non_existent_message_it_should_display_no_mail_message(
    capsys, good_loaded_config, args_save, mailnumber
):
    args_save.mailnumber = mailnumber
    expected_message = f"No mail found with number {mailnumber}\n"

    curmaildir = good_loaded_config["maildir"] / "new"
    assert 0 < sum(1 for _ in curmaildir.iterdir()) < 5

    patch_config = mock.patch("wemail.load_config", return_value=good_loaded_config)
    with patch_config:
        wemail.do_it_two_it(args_save)

    captured = capsys.readouterr()
    assert captured.out == expected_message


def test_when_save_is_called_on_message_that_exists_it_should_go_to_save_folder(
    good_loaded_config, temp_maildir
):
    temp_maildir = pathlib.Path(temp_maildir) / "new"
    target_folder = temp_maildir.parent / "blarp"
    count = 0
    for file in wemail.sorted_mailfiles(maildir=temp_maildir):
        count += 1
        assert file.exists(), "Mailfile should have been there"
        wemail.save(
            config=good_loaded_config,
            maildir=temp_maildir,
            mailnumber=1,
            target_folder=target_folder,
        )
        assert not file.exists(), "Mailfile failed to be removed"
        assert (
            target_folder / file.name
        ).exists(), "Mailfile was not saved in target folder"
    assert count > 0, "We did not actually check saving any files"


def test_when_save_is_called_it_should_print_which_message_was_saved(
    capsys, good_loaded_config, temp_maildir
):
    temp_maildir = pathlib.Path(temp_maildir) / "new"
    target_folder = temp_maildir.parent / "fnord"
    expected_message = (
        "Moved message from person.man@example.com - 'I hate you' to fnord.\n"
    )

    wemail.save(
        config=good_loaded_config,
        maildir=temp_maildir,
        mailnumber=1,
        target_folder=target_folder,
    )

    captured = capsys.readouterr()
    assert captured.out == expected_message


# End save email tests }}}

# {{{ commonmark(down) tests
def test_commonmarkdown_should_produce_marked_multipart_message(good_draft):
    good_draft["X-CommonMark"] = "True"
    plaintext = "This *is* ***my*** message"
    markedtext = mistletoe.markdown(plaintext)
    good_draft.set_content(plaintext)
    expected_plaintext = good_draft.get_content()
    msg = wemail.commonmarkdown(good_draft)
    assert msg.get_body(("html",)).get_payload() == markedtext
    assert msg.get_body(("plain",)).get_payload() == expected_plaintext


def test_commonmarkdown_should_strip_x_commonmark_header(good_draft):
    good_draft["X-CommonMark"] = "True"
    good_draft.set_content("fnord")
    msg = wemail.commonmarkdown(good_draft)
    assert "X-CommonMark" not in msg


def test_if_no_commonmark_header_commonmarkdown_should_return_msg(good_draft):
    # This is just to make sure we don't accidentally add the header to
    # the good_draft
    assert "X-CommonMark" not in good_draft

    msg = wemail.commonmarkdown(good_draft)

    assert msg is good_draft


# }}}

# {{{ attachify tests
def test_if_attachify_msg_has_no_attachments_it_should_return_original_msg(good_draft):
    # Double check we don't accidentally add attachments where they're
    # not wanted...
    assert "Attachment" not in good_draft

    msg = wemail.attachify(good_draft)

    assert msg is good_draft


def test_attachify_should_attach_text_file_with_proper_type(good_draft):
    with tempfile.TemporaryDirectory() as td:
        file = pathlib.Path(td, "fnord.txt")
        expected_content = "this is some text\n"
        file.write_text(expected_content)
        good_draft["Attachment"] = str(file)

        msg = wemail.attachify(good_draft)

        attachment = next(msg.iter_attachments())

        assert attachment.get_content_type() == "text/plain"
        assert attachment.get_content() == expected_content


def test_attachify_with_no_guessed_type_should_attach_as_application_octet_stream(
    good_draft
):
    with tempfile.TemporaryDirectory() as td:
        file = pathlib.Path(td, "fnord.bar")
        expected_content = b"this is some content"
        file.write_bytes(expected_content)
        good_draft["Attachment"] = str(file)

        msg = wemail.attachify(good_draft)

        attachment = next(msg.iter_attachments())

        assert attachment.get_content_type() == "application/octet-stream"
        assert attachment.get_content() == expected_content


def test_attachify_with_inline_image_file_should_attach_it_inline(good_draft):
    with tempfile.TemporaryDirectory() as td:
        file = pathlib.Path(td, "fnord.png")
        png_header = b"\x89PNG\x0d\n\x1a\n"
        expected_content = png_header + b"not a real png"
        file.write_bytes(expected_content)
        good_draft["Attachment"] = f"{str(file)}; inline=True"

        msg = wemail.attachify(good_draft)

        attachment = next(msg.iter_attachments())

        assert attachment.get_content_type() == "image/png"
        assert attachment.get_content() == expected_content
        assert attachment.get_content_disposition() == "inline"


def test_attachify_with_noninline_image_file_should_regular_attach_it(good_draft):
    with tempfile.TemporaryDirectory() as td:
        file = pathlib.Path(td, "fnord.png")
        png_header = b"\x89PNG\x0d\n\x1a\n"
        expected_content = png_header + b"not a real png"
        expected_filename = "fizzy.png"
        file.write_bytes(expected_content)
        good_draft["Attachment"] = f'{str(file)}; inline=False; name="fizzy.png"'

        msg = wemail.attachify(good_draft)

        attachment = next(msg.iter_attachments())

        assert attachment.get_content_type() == "image/png"
        assert attachment.get_content() == expected_content
        assert attachment.get_content_disposition() == "attachment"
        assert attachment.get_filename() == expected_filename


def test_attachify_with_audio_file_should_attach_with_audio(good_draft):
    with tempfile.TemporaryDirectory() as td:
        file = pathlib.Path(td, "fnord.wav")
        expected_content = b"RIFF$\0\0\0WAVEfmt \n\0\0\0\x01\0\x01\0D\xc2\0\0\x88X\x01\0\x02\0\n0data\0\0\0\0"
        file.write_bytes(expected_content)
        good_draft["Attachment"] = f"{str(file)}"

        msg = wemail.attachify(good_draft)

        attachment = next(msg.iter_attachments())

        assert attachment.get_content_type() == "audio/x-wav"
        assert attachment.get_content() == expected_content
        assert attachment.get_content_disposition() == "attachment"
        assert attachment.get_filename() == file.name


# }}}

# {{{ action_prompt test


def test_when_action_prompt_not_in_valid_input_it_should_prompt_again(capsys):
    bad_input = "p"
    good_input = "s"
    expected_out = f"{bad_input!r} not a valid input.\n"
    with mock.patch("builtins.input", side_effect=[bad_input, good_input]):
        wemail.action_prompt()
    captured = capsys.readouterr()

    assert captured.out == expected_out


# }}} end action_prompt test

# {{{ Decode subject test


def test_when_subject_is_encoded_it_should_be_properly_decoded():
    encoded_subject = "Subject: =?iso-8859-1?q?p=F6stal?="
    expected_subject = "Subject: p\xf6stal"

    actual_subject = wemail.decode_subject(encoded_subject)

    assert actual_subject == expected_subject


# }}}

# {{{ get_sender tests


def test_when_only_one_recipient_it_should_be_returned(good_loaded_config):
    msg = wemail._parser.parsebytes(
        textwrap.dedent(
            """\
        To: Foo Man <test(whatever)@example.com>'
    """
        ).encode()
    )

    actual = wemail.get_sender(msg=msg, config=good_loaded_config)

    assert actual == "Foo Man <test@example.com>"


def test_when_more_than_one_recipient_with_one_found_in_config_it_should_use_config(
    good_loaded_config
):
    msg = wemail.EmailMessage()
    msg[
        "To"
    ] = "foo@bar.example.com, Roscivs Le Bottia <roscivs@indessed(not you).example.com>"

    actual = wemail.get_sender(msg=msg, config=good_loaded_config)

    assert actual == good_loaded_config["roscivs@indessed.example.com"]["from"]


def test_when_recipient_in_config_has_no_from_set_it_should_fallback_to_provided_email(
    good_loaded_config
):
    msg = wemail.EmailMessage()
    msg["To"] = "foo@bar.example.com, Nowhere Man <no(whatever).from@example.com>"

    actual = wemail.get_sender(msg=msg, config=good_loaded_config)

    assert actual == "Nowhere Man <no.from@example.com>"


def test_when_multiple_recipients_are_found_in_config_it_should_ask_which_to_use(
    capsys, good_loaded_config
):
    msg = wemail.EmailMessage()
    msg[
        "To"
    ] = "foo@bar.example.com, Nowhere Man <no(whatever).from@example.com>, roscivs@indessed.example.com"

    with mock.patch("builtins.input", side_effect=["9", "2"]) as fake_input:
        actual = wemail.get_sender(msg=msg, config=good_loaded_config)
        fake_input.assert_has_calls(
            [
                mock.call("Use which address? [1-2]: "),
                mock.call("Use which address? [1-2]: "),
            ]
        )

    captured = capsys.readouterr()
    assert actual == good_loaded_config["roscivs@indessed.example.com"]["from"]
    assert (
        captured.out == "Found multiple possible senders:\n"
        "1. Nowhere Man <no.from@example.com>\n"
        "2. Roscivs Bottia <roscivs@indessed.example.com>\n"
        "Invalid choice '9'\n"
    )


# }}} end get_sender tests

# {{{ get_msg_date tests


def test_get_msg_date_should_provide_utc_if_tzinfo_not_on_date(temp_maildir):
    file = pathlib.Path(temp_maildir, "fnord.eml")
    file.write_text(
        textwrap.dedent(
            """\
        From: whoever
        To: whoever
        Subject: whatever
        Date: Sat, 8 Jun 2019 11:45:15
        """
        )
    )
    date = wemail.get_msg_date(file)

    assert date.tzinfo == wemail.timezone.utc


def test_get_msg_date_should_use_present_timezone_if_present_on_date(temp_maildir):
    file = pathlib.Path(temp_maildir, "fnord.eml")
    file.write_text(
        textwrap.dedent(
            """\
        From: whoever
        To: whoever
        Subject: whatever
        Date: Sat, 8 Jun 2019 11:45:15 -0600
        """
        )
    )
    date = wemail.get_msg_date(file)

    assert date.tzinfo == wemail.timezone(datetime.timedelta(hours=-6))


# }}} end get_msg_date tests
