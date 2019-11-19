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

from collections import namedtuple
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


@pytest.fixture(scope="module")
def testdir():
    with tempfile.TemporaryDirectory() as dirname:
        yield dirname


@pytest.fixture(scope="module")
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
        yield dirname


@pytest.fixture()
def good_config(temp_maildir):
    file = io.StringIO()
    json.dump({
        "maildir": temp_maildir,
    }, file)
    file.seek(0)
    return file


@pytest.fixture()
def args_new_alone(good_config):
    Namespace = namedtuple("Namespace", "action,config")
    return Namespace(action="new", config=good_config)


def test_when_action_is_new_it_should_do_new(args_new_alone):
    fake_do_new = mock.MagicMock()
    with mock.patch("wemail.do_new", fake_do_new):
        wemail.do_it_two_it(args_new_alone)
        fake_do_new.assert_called()


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
        '''
        From: whatever@example.com
        To: person_man@example.com
        Subject: Testing 123
        '''
    ).strip()

    with tempfile.TemporaryDirectory() as dirname:
        maildir = pathlib.Path(dirname)
        drafts = maildir / 'drafts'
        config = {"maildir": maildir}
        wemail.create_draft(template=template, config=config)

        files = list(drafts.iterdir())
        assert len(files) == 1
        assert files[0].read_text() == template


def test_create_drafts_should_use_created_filename():
    # TODO: DRY this out with the other one -W. Werner, 2019-11-17
    template = textwrap.dedent(
        '''
        From: whatever@example.com
        To: person_man@example.com
        Subject: Testing 123
        '''
    ).strip()
    expected_name = 'blargle.eml'

    with tempfile.TemporaryDirectory() as dirname:
        maildir = pathlib.Path(dirname)
        drafts = maildir / 'drafts'
        config = {"maildir": maildir}
        fake_draftname = mock.MagicMock(return_value=expected_name)
        with mock.patch('wemail._make_draftname', fake_draftname):
            wemail.create_draft(template=template, config=config)

        assert (drafts / expected_name).read_text() == template


##########################
# End Create Draft Tests #
##########################


def test_make_draftname_should_be_combination_of_the_current_timestamp_and_subject():
    subject = 'Something Cool'
    expected_draftname = '20100824000000-Something-Cool.eml'
    actual_draftname = wemail._make_draftname(subject=subject, timestamp=datetime.datetime(2010, 8, 24, 0, 0, 0))

    assert actual_draftname == expected_draftname


########################
# Create maildir tests #
########################

def test_ensure_maildirs_exist_should_create_proper_dirs():
    with tempfile.TemporaryDirectory() as dirname:
        maildir = pathlib.Path(dirname)
        wemail.ensure_maildirs_exist(maildir=maildir)

        assert (maildir / 'cur').exists()
        assert (maildir / 'new').exists()
        assert (maildir / 'drafts').exists()
        assert (maildir / 'outbox').exists()
        assert (maildir / 'sent').exists()


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
        with wemail.Message(config={}) as draft:
            draft.send(smtp)


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
    testdir
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
