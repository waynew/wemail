from setuptools import setup


tests_require = [
    'pytest',
]
setup(
    name='wemail',
    version='0.1.2',
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
