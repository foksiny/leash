from setuptools import setup, find_packages

setup(
    name='leash',
    version='0.17.0 Beta',
    packages=find_packages(),
    install_requires=[
        'llvmlite'
    ],
    entry_points={
        'console_scripts': [
            'leash=leash.cli:main',
            'leashed=leash.leashed:main',
        ],
    },
)
