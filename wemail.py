import os
import subprocess
import sys
import tempfile
from cmd import Cmd
from email.header import decode_header
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path


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
            print(msg.date, msg['From'])
            print(msg.subject)
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type.startswith('multipart'):
                    continue
                show = input(f'Content type is {content_type}'
                             f' - Display in {self.editor}? [y/N]: ')
                if show.strip().lower() in ('y', 'yes'):
                    with tempfile.NamedTemporaryFile() as f:
                        f.write(part.get_payload().encode())
                        f.flush()
                        subprocess.run([self.editor, f.name])
            action, _, rest = input('Action: ').partition(' ')
            if action == 'quit':
                return
            elif action in ('s', 'save'):
                folder = rest
                self.mailbox.move_to(key=msg.key, folder=folder)
            elif action in ('d', 'del'):
                confirm = input('Really delete? [Y/n]: ')
                if confirm.strip().lower() in ('', 'y', 'yes'):
                    self.mailbox.delete(key=msg.key)

    do_q = do_quit


class WeMaildir:
    def __init__(self, path):
        self.path = Path(path)
        self._parser = BytesParser()

    def __iter__(self):
        for file in (self.path / 'cur').iterdir():
            with file.open('rb') as f:
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
                yield msg

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
        emaildir = Path(sys.argv[0])
    else:
        emaildir = Path(os.environ.get('WEMAIL_DIR', '~/Maildir'))

    mailbox = WeMaildir(emaildir)
    # TODO: Try to import curses and do a curses UI -W. Werner, 2019-05-10
    # It should basically be a clone(ish) of Alpine, which I love, but isn't
    # quite as awesome as I really want.
    # The exceptional case shoule be running the CliMail - or if there was
    # an option passed on the command line
    CliMail(mailbox).cmdloop()


if __name__ == '__main__':
    do_it()
