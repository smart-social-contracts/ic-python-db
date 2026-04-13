from setuptools import find_packages, setup

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="ic-python-db",
    version="0.7.9",
    author="Smart Social Contracts",
    author_email="smartsocialcontracts@gmail.com",
    description="A lightweight key-value database with entity relationships and audit logging",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/smart-social-contracts/ic-python-db",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3.10",
    ],
    python_requires=">=3.7",
    install_requires=[],
)
