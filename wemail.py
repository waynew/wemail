import os
import subprocess
import sys
import tempfile
from cmd import Cmd
from email.header import decode_header
from email.parser import BytesParser
from email.policy import EmailPolicy
from email.utils import parsedate_to_datetime
from pathlib import Path
from textwrap import dedent


EDITOR = os.environ.get('EDITOR', 'nano')
POLICY = EmailPolicy(utf8=True)


class MsgPrompt(Cmd):
    def __init__(self, *, mailbox, key):
        super().__init__()
        self.mailbox = mailbox
        self.msg = mailbox[key]
        self.intro = dedent(f'''\
        From: {self.msg["From"]}
        To: {', '.join(self.msg.get_all('To'))}
        Date: {self.msg.date}
        Subject: {self.msg.subject}
        ''')
        self.prompt = "action> "

    def do_quit(self, line):
        return True

    def do_EOF(self, line):
        print()
        return self.do_quit(line)

    def do_delete(self, line):
        try:
            if input('Really delete? [Y/n]: ').lower() not in ('n','no'): 
                self.mailbox.delete(key=self.msg.key)
        except KeyboardInterrupt:
            print('Okay, never mind!')
        else:
            return True

    def do_save(self, line):
        self.mailbox.move_to(key=self.msg.key, folder=line.strip())
        print('Saved!')
        return True

    def do_parts(self, line):
        parts = []
        i = 1
        for part in self.msg.walk():
            content_type = part.get_content_type()
            if content_type.startswith('multipart/'):
                if not line:
                    print(content_type)
            else:
                parts.append(part)
                if not line:
                    print(f'\t{i}. {content_type}')
                i += 1
        choice = input(f'Edit which part? (1-{i-1}): ')
        if choice.strip():
            try:
                choice = int(choice)-1
                with tempfile.NamedTemporaryFile(suffix='.eml') as f:
                    f.write(parts[choice].get_payload(decode=True))
                    f.flush()
                    subprocess.run([EDITOR, f.name])
            except ValueError:
                # Avoid growing memory for a typo
                parts.clear()
                return self.do_parts('')

    # Aliases
    do_d = do_del = do_delete
    do_q = do_quit
    do_s = do_m = do_move = do_save


class CliMail(Cmd):
    def __init__(self, mailbox):
        super().__init__()
        self.mailbox = mailbox
        self.editor = os.environ.get('EDITOR', 'nano')

    @property
    def prompt(self):
        return f'WEmail - {self.mailbox.msg_count}> '

    def do_quit(self, line):
        print('Okay bye!')
        return True

    def do_EOF(self, line):
        print()
        return self.do_quit(line)

    def do_ls(self, line):
        if not line.strip():
            for msg in self.mailbox:
                print(msg.date, msg['From'])

    def do_proc(self, line):
        for msg in self.mailbox:
            MsgPrompt(mailbox=self.mailbox, key=msg.key).cmdloop()
#            print(msg.date, msg['From'])
#            print(msg.subject)
#            for part in msg.walk():
#                content_type = part.get_content_type()
#                if content_type.startswith('multipart'):
#                    continue
#                show = input(f'Content type is {content_type}'
#                             f' - Display in {self.editor}? [y/N]: ')
#                if show.strip().lower() in ('y', 'yes'):
#                    with tempfile.NamedTemporaryFile() as f:
#                        f.write(part.get_payload().encode())
#                        f.flush()
#                        subprocess.run([self.editor, f.name])
#            action, _, rest = input('Action: ').partition(' ')
#            if action == 'quit':
#                return
#            elif action in ('s', 'save'):
#                folder = rest
#                self.mailbox.move_to(key=msg.key, folder=folder)
#            elif action in ('d', 'del'):
#                confirm = input('Really delete? [Y/n]: ')
#                if confirm.strip().lower() in ('', 'y', 'yes'):
#                    self.mailbox.delete(key=msg.key)

    do_q = do_quit


class WeMaildir:
    def __init__(self, path):
        self.path = Path(path)
        self._parser = BytesParser()

    def __iter__(self):
        for file in (self._curpath).iterdir():
            yield self[file.name]

    def __getitem__(self, key):
        mailfile = self._curpath / key
        with mailfile.open('rb') as f:
            msg = self._parser.parse(f)
            msg.key = f.name
            msg.date = parsedate_to_datetime(msg['Date'])
            subject = ''
            for part in decode_header(msg['subject']):
                data, encoding = part
                if encoding is None:
                    subject += data
                else:
                    subject += data.decode(encoding)
            msg.subject = subject
            return msg

    @property
    def _newpath(self):
        return self.path / 'new'

    @property
    def _curpath(self):
        return self.path / 'cur'

    @property
    def msg_count(self):
        return sum(1 for _ in self._curpath.iterdir())

    @property
    def has_new(self):
        return any(self._newpath.iterdir())

    def check_new(self):
        '''
        Check for mail in /new.

        If new mail is found, move it to /cur and return True.
        If no new mail is found, return False.
        '''
        has_any = False
        for file in self._newpath.iterdir():
            has_any = True
            newname = self._curpath / file.name
            file.rename(newname)
        return has_any

    def move_to(self, *, key, folder):
        '''
        Move the message given by ``key`` to the provided ``folder``.
        '''
        f = self._curpath / key
        newname = self.path / folder / f.name
        newname.parent.mkdir(parents=True, exist_ok=True)
        f.rename(newname)

    def delete(self, key):
        '''
        Delete the message given by ``key``.
        '''
        (self._curpath / key).unlink()


def do_it():  # Shia LeBeouf!
    if len(sys.argv) > 1:
        emaildir = Path(sys.argv[1])
    else:
        emaildir = Path(os.environ.get('WEMAIL_DIR', '~/Maildir'))

    if not emaildir.exists():
        sys.exit(f'Maildir {str(emaildir)!r} does not exist.')

    mailbox = WeMaildir(emaildir)
    # TODO: Try to import curses and do a curses UI -W. Werner, 2019-05-10
    # It should basically be a clone(ish) of Alpine, which I love, but isn't
    # quite as awesome as I really want.
    # The exceptional case shoule be running the CliMail - or if there was
    # an option passed on the command line
    try:
        CliMail(mailbox).cmdloop()
    except KeyboardInterrupt:
        print()
        print('^C caught, goodbye!')


if __name__ == '__main__':
    do_it()
