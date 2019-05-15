from pathlib import Path
from setuptools import setup

wemail = Path(__file__).parent / 'wemail.py'
with wemail.open('r') as f:
    for line in f:
        if line.startswith('__version__'):
            __version__ = line.partition('=')[-1].strip().strip('"').strip("'")
            break

tests_require = [
    'pytest',
]
setup(
    name='wemail',
    version=__version__,
    author='Wayne Werner',
    author_email='waynejwerner@gmail.com',
    url='https://github.com/waynew/wemail',
    py_modules=['wemail'],
    entry_points='''
    [console_scripts]
    wemail=wemail:do_it
    ''',
    tests_require=tests_require,
    extras_require={
        'test': tests_require,
        'build': ['wheel'],
    },
)
