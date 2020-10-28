# We've Moved!

This project has moved to https://gitlab.com/waynew/wemail

Find us over there!

# WEmail

(Or, Wayne's Email)

I really love command line tools. Alpine has been a favorite of mine and Mutt
is also a rather useful mail client. But I wanted to do some things
differently, so I'm working on this project, WEmail.

It's designed for use with my other project [Orouboros][1], a mailserver that
cheerfully violates all kinds of standards for MTAs.


To get started, you'll want to create a `~/.wemailrc` file. It's a JSON file,
because aside from the pesky inability to have trailing commas, it's pretty
good otherwise. Here's a sample one to get you started:

```
{
    "ABORT_TIMEOUT": 2,
    "MAILDIR": "~/mymail/",
    "DEFAULT_FROM": "person@example.com",
    "person@example.com": {
        "HEADERS": {
            "From": "person@example.com",
            "To": "",
            "X-CommonMark": "True",
            "Subject": ""
        },
        "SMTP_HOST": "example.com",
        "SMTP_PORT": 1234,
        "SMTP_USE_TLS": true,
        "SMTP_USERNAME": "person",
        "SMTP_PASSWORD": "this is not a real password",
        "":""
    }
}
```

Oh yeah, you can add `"":""` at the end of your blocks, because I don't care
about them, and that lets you put commas at the end of everything else.

Anyway, after that you just `python3 -m pip install --user wemail` and then run
`wemail`.

Now you can run `check` to check your email, and `proc` to process your email.
While not everything has a `help`, hopefully there should be enough to get you
going. And if you get stuck, feel free to send *me* an email - you can find my
address in `setup.py`. Oooh, maybe I should add a command that lets you send me
an email for help!


[1]: https://pypi.org/project/orouboros/
