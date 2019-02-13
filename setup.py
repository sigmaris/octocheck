from setuptools import find_packages, setup

setup(
    name='octocheck',
    version='0.0.1',
    description='A reporter utility for Github Checks',
    author='Hugh Cole-Baker',
    author_email='sigmaris@gmail.com',
    url="https://github.com/sigmaris/octocheck",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        'github3.py ~= 1.3.0',
    ],
    entry_points='''
        [console_scripts]
        octocheck=octocheck:cli
    ''',
    classifiers=[
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
    ]
)
