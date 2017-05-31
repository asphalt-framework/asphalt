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
    long_description=Path(__file__).with_name('README.rst').read_text('utf-8'),
    author='Alex Grönholm',
    author_email='alex.gronholm@nextday.fi',
    url='https://github.com/asphalt-framework/asphalt',
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',
        'Topic :: Software Development :: Libraries :: Application Frameworks',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3 :: Only',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6'
    ],
    license='Apache License 2.0',
    zip_safe=False,
    packages=[
        'asphalt.core'
    ],
    setup_requires=[
        'setuptools >= 24.2.1',
        'setuptools_scm >= 1.7.0'
    ],
    python_requires='>= 3.5.2',
    install_requires=[
        'setuptools',  # this is here to tell downstream packagers that it needs pkg_resources
        'ruamel.yaml >= 0.12',
        'typeguard ~= 2.0',
        'async-generator ~= 1.4',
        'asyncio_extras ~= 1.3',
        'click >= 6.6'
    ],
    extras_require={
        'uvloop': ['uvloop >= 0.4.10'],
        'gevent': ['aiogevent >= 0.2'],
        'testing': [
            'pytest',
            'pytest-asyncio',
            'pytest-catchlog',
            'pytest-cov'
        ]
    },
    entry_points={
        'console_scripts': [
            'asphalt = asphalt.core.cli:main'
        ],
        'asphalt.core.event_loop_policies': [
            'uvloop = asphalt.core.runner:uvloop_policy [uvloop]',
            'gevent = asphalt.core.runner:gevent_policy [gevent]'
        ]
    }
)
