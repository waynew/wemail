import io
import datetime
import json
import pathlib
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


class MyHandler:
    def __init__(self):
        self.box = []

    async def handle_DATA(self, server, session, envelope):
        self.box.append(envelope)
        return "250 OK"


parser = wemail.make_parser()


@pytest.fixture()
def sample_good_mailfile():
    with tempfile.TemporaryDirectory() as dirname:
        mailfile = pathlib.Path(dirname) / "test.eml"
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


@pytest.fixture(scope="module")
def testdir():
    with tempfile.TemporaryDirectory() as dirname:
        yield dirname


@pytest.fixture()
def test_server():
    try:
        handler = MyHandler()
        controller = Controller(handler)
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
            fname.write_text(
                """From: person.man@example.com\nTo: triangle.man@example.com\nSubject: I hate you\n\nLet's have a fight!"""
            )
        yield dirname


@pytest.fixture()
def good_config(temp_maildir):
    file = io.StringIO()
    json.dump({"maildir": temp_maildir}, file)
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
            config=good_loaded_config, mailfile=args_reply.mailfile
        )


def test_when_action_is_reply_all_it_should_reply_all(
    args_reply_all, good_loaded_config
):
    patch_reply_all = mock.patch("wemail.reply_all", autospec=True)
    patch_config = mock.patch("wemail.load_config", return_value=good_loaded_config)
    with patch_reply_all as fake_reply_all, patch_config:
        wemail.do_it_two_it(args_reply_all)
        fake_reply_all.assert_called_with(
            config=good_loaded_config, mailfile=args_reply_all.mailfile
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


def test_when_version_is_passed_it_should_display_version(
    args_version, good_loaded_config
):
    ...


########################
# Start Template Tests #
########################


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


######################
# End Template Tests #
######################


############################
# Start Create Draft Tests #
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


##########################
# End Create Draft Tests #
##########################


def test_make_draftname_should_be_combination_of_the_current_timestamp_and_subject():
    subject = "Something Cool"
    expected_draftname = "20100824000000-Something-Cool.eml"
    actual_draftname = wemail._make_draftname(
        subject=subject, timestamp=datetime.datetime(2010, 8, 24, 0, 0, 0)
    )

    assert actual_draftname == expected_draftname


########################
# Create maildir tests #
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


#####################
# End maildir tests #
#####################


#####################
# Check email tests #
#####################


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


def test_send_email_should_send_provided_email(sample_good_mailfile, test_server):
    config = {
        "SMTP_HOST": test_server.hostname,
        "SMTP_PORT": test_server.port,
    }

    expected_message = wemail._parser.parsebytes(sample_good_mailfile.read_bytes())
    wemail.send(config=config, mailfile=sample_good_mailfile)

    actual_message = wemail._parser.parsebytes(test_server.handler.box[0].content)

    for header in ("from", "to", "subject"):
        assert actual_message[header] == expected_message[header]

    assert actual_message.get_payload() == expected_message.get_payload()


def test_send_should_display_sending_status(capsys, sample_good_mailfile):
    expected_message = (
        'Sending "why don\'t you like me?" to Triangle Man <triangle@example.com>'
        "\n"
        "Done!"
        "\n"
    )
    with mock.patch("wemail.send_message", autospec=True):
        wemail.send(config={}, mailfile=sample_good_mailfile)

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
    expected_msg = "Not really rendered"
    patch_send_message = mock.patch(
        "wemail.send_message", mock.MagicMock(return_value=expected_msg)
    )

    with mock.patch(
        "wemail.commonmarkdown", autospec=True
    ) as fakemarkdown, patch_send_message as fake_sm:
        wemail.send(config={}, mailfile=sample_good_mailfile)
        fakemarkdown.assert_called()
        fake_sm.assert_called_with(
            msg=expected_msg,
            smtp_host=mock.ANY,
            smtp_port=mock.ANY,
            use_tls=mock.ANY,
            use_smtps=mock.ANY,
            username=mock.ANY,
            password=mock.ANY,
        )


# Below here? Not sure what's what!
###########################


@pytest.fixture()
def good_draft(testdir, goodheaders):
    with wemail.Message(draftdir=testdir, headers=goodheaders) as draft:
        yield draft


@pytest.mark.skip
def test_when_composing_an_email_a_file_should_be_created_in_draft_dir(goodconfig):
    with wemail.Message(config=goodconfig) as composer:
        assert composer.filename.exists()


@pytest.mark.skip
def test_when_block_exits_naturally_draft_should_be_deleted(goodconfig):
    with wemail.Message(config=goodconfig) as composer:
        pass

    assert not composer.filename.exists()


@pytest.mark.skip
def test_when_block_exits_exceptionally_draft_should_stay(goodconfig):
    class Foo(Exception):
        pass

    try:
        with wemail.Message(config=goodconfig) as composer:
            raise Foo("Bye")
    except Foo:
        pass

    assert composer.filename.exists()


@pytest.mark.skip
def test_default_headers_should_be_set_on_resulting_email(goodconfig):
    with wemail.Message(config=goodconfig) as composer:
        msg = composer.msg
        for header in goodconfig["default_headers"]:
            assert msg[header] == str(goodconfig["default_headers"][header])


@pytest.mark.skip
def test_creating_simple_email(test_server):
    with smtplib.SMTP(test_server.hostname, test_server.port) as smtp:
        from email.mime.text import MIMEText as e

        msg = e("Hello worlds")
        msg["From"] = "me@example.com"
        msg["To"] = "you@example.com"
        smtp.send_message(msg)
        assert "" == dir(test_server.handler.box[0])


def test_if_draft_exception_happens_draft_file_should_still_exist(testdir):
    class Foo(Exception):
        pass

    try:
        with wemail.Message(draftdir=testdir) as draft:
            filename = draft.filename
            raise Foo()
    except Foo:
        pass

    assert filename.exists()


def test_if_draft_block_exits_without_exception_draft_should_be_gone(testdir):
    with wemail.Message(draftdir=testdir) as draft:
        filename = draft.filename
    assert not filename.exists(), f"{filename!s} exists!"


def test_if_draft_save_is_called_with_no_arguments_draft_should_stay_around(testdir):
    with wemail.Message(draftdir=testdir) as draft:
        filename = draft.filename
        draft.save()
    assert filename.exists(), f"{filename!s} does not exist!"


def test_if_draft_save_is_called_with_same_name_as_filename_it_should_stay(testdir):
    with wemail.Message(draftdir=testdir) as draft:
        filename = draft.filename
        draft.save(filename=filename)
    assert filename.exists(), f"{filename!s} does not exist!"


def test_if_draft_save_is_called_with_different_name_new_should_exist_old_should_be_gone(
    testdir,
):
    with wemail.Message(draftdir=testdir) as draft:
        filename = draft.filename
        new_filename = filename.with_suffix(".blerp")
        draft.save(filename=new_filename)
    assert not filename.exists()
    assert new_filename.exists()


def test_provided_headers_should_be_set_on_message(testdir):
    headers = {
        "From": "roscivs@indessed.example.com",
        "To": "wayne@example.com",
        "Subject": "testing",
    }
    with wemail.Message(draftdir=testdir, headers=headers) as draft:
        msg = draft.msg
        for header in headers:
            assert msg[header] == headers[header]


def test_draft_should_leave_a_parseable_message_on_disk(good_draft, goodheaders):
    with good_draft.filename.open(mode="rb") as f:
        msg = wemail._parser.parse(f)
    for header in goodheaders:
        assert msg[header] == goodheaders[header]


def test_message_edited_elsewhere_should_be_returned_by_draft_mst(good_draft):
    expected_content = "\n\nThis is super cool!"
    with good_draft.filename.open(mode="a") as f:
        f.write(expected_content)
    assert good_draft.msg.get_payload() == expected_content


def test_commonmarkdown_should_produce_marked_multipart_message(good_draft):
    msg = good_draft.msg
    msg["X-CommonMark"] = "True"
    plaintext = "This *is* ***my*** message"
    markedtext = mistletoe.markdown(plaintext)
    msg.set_content(plaintext)
    expected_plaintext = msg.get_content()
    msg = wemail.commonmarkdown(msg)
    assert msg.get_body(("html",)).get_payload() == markedtext
    assert msg.get_body(("plain",)).get_payload() == expected_plaintext


def test_commonmarkdown_should_strip_x_commonmark_header(good_draft):
    msg = good_draft.msg
    msg["X-CommonMark"] = "True"
    msg = wemail.commonmarkdown(msg)
    assert "X-CommonMark" not in msg


def test_if_no_commonmark_header_commonmarkdown_should_return_msg(good_draft):
    original_msg = good_draft.msg
    # This is just to make sure we don't accidentally add the header to
    # the good_draft
    assert "X-CommonMark" not in original_msg

    msg = wemail.commonmarkdown(original_msg)

    assert msg is original_msg


def test_if_attachify_msg_has_no_attachments_it_should_return_original_msg(good_draft):
    original_msg = good_draft.msg
    # Double check we don't accidentally add attachments where they're
    # not wanted...
    assert "Attachment" not in original_msg

    msg = wemail.attachify(original_msg)

    assert msg is original_msg


# TODO: Add more attachify tests -W. Werner, 2019-06-19


def test_replyify_should_keep_attachments_if_True(good_draft):
    original_msg = wemail.EmailMessage()
    original_msg["From"] = "fnord@example.com"
    original_msg.set_content("Hello!")
    expected_content = b"This is my attachment"
    expected_filename = "blerp"
    original_msg.add_attachment(
        expected_content,
        maintype="application",
        subtype="octet-stream",
        filename=expected_filename,
    )

    msg = wemail.replyify(
        msg=original_msg, keep_attachments=True, sender="foo@example.com"
    )

    attachment = next(msg.iter_attachments())
    assert attachment.get_filename() == expected_filename
    assert attachment.get_content() == expected_content


def test_replyify_should_strip_attachments_if_keep_is_False():
    original_msg = wemail.EmailMessage()
    original_msg["From"] = "roscivs@example.com"
    original_msg.set_content("This is my message")
    original_msg.add_attachment(b"asdf", maintype="application", subtype="octet-stream")

    msg = wemail.replyify(
        msg=original_msg, keep_attachments=False, sender="foo@example.com"
    )

    with pytest.raises(StopIteration):
        next(msg.iter_attachments())


def test_replyify_should_set_to_and_cc_to_originals_if_reply_all():
    original_msg = wemail.EmailMessage()
    original_msg["From"] = "roscivs@example.com"
    original_msg["To"] = "Test <me@example.com>, You <you@example.org>"
    original_msg["Cc"] = "Karbon <k@example.com>, Copy <copy@example.net>"
    expected_to = getaddresses(
        original_msg.get_all("From") + original_msg.get_all("To")
    )
    expected_cc = getaddresses(original_msg.get_all("Cc"))

    msg = wemail.replyify(msg=original_msg, reply_all=True, sender="foo@example.com")
    actual_to = getaddresses(msg.get_all("To"))
    actual_cc = getaddresses(msg.get_all("Cc"))

    assert actual_to == expected_to
    assert actual_cc == expected_cc


def test_replyify_should_set_to_to_original_from_if_not_reply_all():
    original_msg = wemail.EmailMessage()
    original_msg["From"] = "roscivs@example.com"

    msg = wemail.replyify(msg=original_msg, reply_all=False, sender="foo@example.com")

    assert getaddresses(msg.get_all("To")) == getaddresses(original_msg.get_all("From"))


def test_replyify_should_use_reply_to_if_it_exists():
    original_msg = wemail.EmailMessage()
    original_msg["From"] = "fnord@example.com"
    original_msg["Reply-To"] = "roscivs@example.com, wayne@example.net"

    msg = wemail.replyify(msg=original_msg, reply_all=False, sender="foo@example.com")

    assert getaddresses(msg.get_all("To")) == getaddresses(
        original_msg.get_all("Reply-To")
    )


def test_replyify_should_set_body_to_quoted_text():
    original_msg = wemail.EmailMessage()
    original_msg["From"] = "fnord@example.com"
    original_msg["Reply-To"] = "roscivs@example.com, wayne@example.net"
    original_msg["Date"] = "Wed, 19 Jun 2019 01:19:06 -0000"
    original_msg.set_content("This is my message, okay?")

    expected = f"""
On Wed, June 19, 2019 at 01:19:06AM, fnord@example.com wrote:
> {original_msg.get_body().get_payload().strip()}
> 
   """.lstrip()[
        :-3
    ]

    msg = wemail.replyify(msg=original_msg, reply_all=False, sender="foo@example.com")

    print(repr(expected))
    print(repr(msg.get_body().get_payload()))
    assert msg.get_body().get_payload() == expected


def test_forwardify_should_keep_attachments_if_True(good_draft):
    original_msg = wemail.EmailMessage()
    original_msg["From"] = "fnord@example.com"
    original_msg.set_content("Hello!")
    expected_content = b"This is my attachment"
    expected_filename = "blerp"
    original_msg.add_attachment(
        expected_content,
        maintype="application",
        subtype="octet-stream",
        filename=expected_filename,
    )

    msg = wemail.forwardify(
        msg=original_msg, keep_attachments=True, sender="foo@example.com"
    )

    attachment = next(msg.iter_attachments())
    assert attachment.get_filename() == expected_filename
    assert attachment.get_content() == expected_content


def test_forwardify_should_strip_attachments_if_keep_is_False():
    original_msg = wemail.EmailMessage()
    original_msg["From"] = "roscivs@example.com"
    original_msg.set_content("This is my message")
    original_msg.add_attachment(b"asdf", maintype="application", subtype="octet-stream")

    msg = wemail.forwardify(
        msg=original_msg, keep_attachments=False, sender="foo@example.com"
    )

    with pytest.raises(StopIteration):
        next(msg.iter_attachments())
