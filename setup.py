from setuptools import setup, find_packages

setup(
    name='leash',
    version='0.1.0',
    packages=find_packages(),
    install_requires=[
        'llvmlite'
    ],
    entry_points={
        'console_scripts': [
            'leash=leash.cli:main',
        ],
    },
)
