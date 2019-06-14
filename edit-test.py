
class EmailEditor:
    def __init__(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, type, message, traceback):
        print(f'Message: {message}')

    @property
    def msg(self):
        return None

    def save(self):
        self._cleanup = False

    def discard(self):
        pass


class Mailbox:
    def send(self, msg):
        pass

    def queue(self, msg):
        pass


if __name__ == '__main__':
    mailbox = Mailbox()
    with EmailEditor() as editor:
        choice = input('[s]end, sa[v]e, [q]ueue, [d]iscard: ')
        if choice == 'v':
            editor.save()
        elif choice == 's':
            mailbox.send(editor.msg)
        elif choice == 'q':
            mailbox.queue(editor.msg)
        elif choice == 'd':
            editor.discard()

