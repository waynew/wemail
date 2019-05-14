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
        self.do_header('')
        self.prompt = "action> "

    def get_part(self, part_num):
        part_num = part_num.strip()
        parts = []
        i = 1
        for part in self.msg.walk():
            content_type = part.get_content_type()
            if content_type.startswith('multipart/'):
                if not part_num:
                    print(content_type)
            else:
                parts.append(part)
                if not part_num:
                    print(f'\t{i}. {content_type}')
                i += 1
        choice = part_num or input(f'Edit which part? (1-{i-1}): ')
        if choice.strip():
            try:
                choice = int(choice)-1
                return parts[choice]
            except ValueError:
                # Avoid growing memory for a typo
                parts.clear()
                return self.get_part('')

    def do_quit(self, line):
        return True

    def do_EOF(self, line):
        print()
        return self.do_quit(line)

    def do_delete(self, line):
        print(f'line x{line}x')
        try:
            if line == ' ' or input('Really delete? [Y/n]: ').lower() not in ('n','no'): 
                self.mailbox.delete(key=self.msg.key)
        except KeyboardInterrupt:
            print('Okay, never mind!')
        else:
            return True

    def do_save(self, line):
        self.mailbox.move_to(key=self.msg.key, folder=line.strip())
        print('Saved!')
        return True

    def complete_save(self, text, line, begidx, endidx):
        return [
            p.name
            for p in self.mailbox.path.glob(text+'*')
        ]

    def do_print(self, line):
        '''
        Print the message part.
        '''
        # TODO: Check terminfo and pause after height lines -W. Werner, 2019-05-12
        part = self.get_part(line)
        if part is None:
            return
        print(part.get_payload(decode=True).decode())

    def do_parts(self, line):
        part = self.get_part(line)
        if part is None:
            return
        with tempfile.NamedTemporaryFile(suffix='.eml') as f:
            f.write(part.get_payload(decode=True))
            f.flush()
            subprocess.call([EDITOR, f.name])

    def do_raw(self, line):
        with tempfile.NamedTemporaryFile(suffix='.eml') as f:
            f.write(self.msg.as_bytes())
            f.flush()
            subprocess.call([EDITOR, f.name])

    def do_header(self, line):
        '''
        Display the message headers.
        '''
        to = self.msg.get_all('To')
        cc = self.msg.get_all('Cc')
        bcc = self.msg.get_all('Bcc')
        recipients = ''
        if to:
            recipients += f"To: {', '.join(to)}"
        if cc:
            recipients += f"Cc: {', '.join(to)}"
        if bcc:
            recipients += f"Bcc: {', '.join(to)}"
        print(dedent(f'''\
        From: {self.msg["From"]}
        Date: {self.msg.date}
        {recipients or 'No recipients headers found???'}
        Subject: {' '.join(self.msg.subject.split(chr(10)))}
        '''))


    # Aliases
    do_d = do_del = do_delete
    do_h = do_header
    do_p = do_parts
    do_q = do_quit
    do_s = do_m = do_move = do_save
    complete_s = complete_m = complete_move = complete_save


class CliMail(Cmd):
    def __init__(self, mailbox):
        super().__init__()
        self.mailbox = mailbox
        self.editor = os.environ.get('EDITOR', 'nano')

    @property
    def prompt(self):
        return f'WEmail - {self.mailbox.msg_count} {self.mailbox.curpath.name}> '

    def do_quit(self, line):
        print('Okay bye!')
        return True

    def do_EOF(self, line):
        print()
        return self.do_quit(line)

    def complete_cd(self, text, line, begidx, endidx):
        return [
            p.name
            for p in self.mailbox.path.glob(text+'*')
        ]

    def do_cd(self, line):
        line = line.lstrip('.')
        if line:
            newpath = self.mailbox.path / line
            if not newpath.is_dir():
                print(f'Error, no directory {newpath}')
            else:
                self.mailbox.curpath = newpath
        else:
            self.mailbox.curpath = self.mailbox._curpath

    def do_ls(self, line):
        if not line.strip():
            for msg in self.mailbox:
                print(msg.date, msg['From'])

    def do_proc(self, line):
        for msg in self.mailbox:
            MsgPrompt(mailbox=self.mailbox, key=msg.key).cmdloop()

    def do_check(self, line):
        '''
        Check for new mail.
        '''
        count = self.mailbox.check_new()
        print(f'{count} new message{"s" if count != 1 else ""}')

    do_q = do_quit


class WeMaildir:
    def __init__(self, path):
        self.path = Path(path)
        self.curpath = self._curpath
        self._parser = BytesParser()

    def __iter__(self):
        for file in (self.curpath).iterdir():
            yield self[file.name]

    def __getitem__(self, key):
        mailfile = self.curpath / key
        with mailfile.open('rb') as f:
            msg = self._parser.parse(f)
            msg.key = f.name
            msg.date = None
            if msg['Date']:
                msg.date = parsedate_to_datetime(msg['Date'])
            subject = ''
            for part in decode_header(msg['subject']):
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
        return self.path / 'new'

    @property
    def _curpath(self):
        return self.path / 'cur'

    @property
    def msg_count(self):
        return sum(1 for _ in self.curpath.iterdir())

    @property
    def has_new(self):
        return any(self._newpath.iterdir())

    def check_new(self):
        '''
        Check for mail in /new.

        If new mail is found, move it to /cur and return True.
        If no new mail is found, return False.
        '''
        count = 0
        for file in self._newpath.iterdir():
            count += 1
            newname = self._curpath / file.name
            file.rename(newname)
        return count

    def move_to(self, *, key, folder):
        '''
        Move the message given by ``key`` to the provided ``folder``.
        '''
        f = self.curpath / key
        newname = self.path / folder / f.name
        newname.parent.mkdir(parents=True, exist_ok=True)
        f.rename(newname)

    def delete(self, key):
        '''
        Delete the message given by ``key``.
        '''
        (self.curpath / key).unlink()


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
