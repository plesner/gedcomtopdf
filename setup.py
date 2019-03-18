from setuptools import setup, find_packages

setup(
  name='gedcomtopdf',
  version='0.0.1',
  packages=find_packages(where="src"),
  install_requires=[
    "babel==2.5.1",
    "gedcompy==0.2.9",
    "six==1.11.0",
    "pdfkit==0.6.1",
    "requests==2.18.4",
    "pillow==4.3.0",
  ],
  entry_points={
    "console_scripts": [
      "gc2pdf=gedcomtopdf:main"
    ]
  })
