from pathlib import Path
import sys

from setuptools import setup

if sys.version_info < (3, 5):
    raise Exception('Asphalt requires at least Python 3.5')

setup(
    name='asphalt',
    use_scm_version={
        'version_scheme': 'post-release',
        'local_scheme': 'dirty-tag'
    },
    description='A microframework for network oriented applications',
    long_description=Path(__file__).parent.joinpath('README.rst').read_text('utf-8'),
    author='Alex Grönholm',
    author_email='alex.gronholm@nextday.fi',
    url='https://github.com/asphalt-framework/asphalt',
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',
        'Topic :: Software Development :: Libraries :: Application Frameworks',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5'
    ],
    license='Apache License 2.0',
    zip_safe=False,
    packages=[
        'asphalt.core'
    ],
    setup_requires=[
        'setuptools_scm >= 1.7.0'
    ],
    install_requires=[
        'setuptools',  # this is here to tell downstream packagers that it needs pkg_resources
        'PyYAML >= 3.11',
        'typeguard >= 1.1.3',
        'asyncio_extras',
        'click >= 6.6'
    ],
    extras_require={
        'uvloop': ['uvloop >= 0.4.10'],
        'gevent': ['aiogevent >= 0.2']
    },
    entry_points={
        'console_scripts': [
            'asphalt = asphalt.core.command:main'
        ],
        'asphalt.core.event_loop_policies': [
            'uvloop = asphalt.core.runner:uvloop_policy [uvloop]',
            'gevent = asphalt.core.runner:gevent_policy [gevent]'
        ]
    }
)
