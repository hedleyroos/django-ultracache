from setuptools import setup, find_packages

setup(
    name="django-ultracache",
    description="Cache views, template fragments and arbitrary Python code. Monitor Django object changes to perform automatic fine-grained cache invalidation from Django level, through proxies, to the browser. Make Django really fast.",
    long_description=open("README.md", "r").read()
    + open("AUTHORS.rst", "r").read()
    + open("CHANGELOG.rst", "r").read(),
    long_description_content_type="text/markdown",
    version="2.3",
    author="Hedley Roos",
    author_email="hedleyroos@gmail.com",
    license="BSD",
    url="http://github.com/hedleyroos/django-ultracache",
    packages=find_packages(),
    dependency_links=[],
    include_package_data=True,
    classifiers=[
        "Programming Language :: Python",
        "License :: OSI Approved :: BSD License",
        "Operating System :: OS Independent",
        "Framework :: Django",
        "Intended Audience :: Developers",
        "Topic :: Internet :: WWW/HTTP :: Dynamic Content",
    ],
    zip_safe=False,
)
