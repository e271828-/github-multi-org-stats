from setuptools import setup, find_packages

setup(
    name='github_contributor_report',
    version='0.1',
    description='A tool to generate reports on GitHub contributions across organizations.',
    author='e271828-',
    author_email='e271828-@users.noreply.github.com',
    packages=find_packages(),
    install_requires=[
        'dash-bootstrap-components',
        'dash_bootstrap_templates',
        'PyGithub',
        'pandas',
        'plotly',
        'dash',
        'fastcore',
        'toolz',
        'python-dotenv',
    ],
)
