"""Micro-benchmark of the Model.__getattribute__ patch.

Measures attribute access cost in three states:

1. Baseline: the original (unpatched) Django Model.__getattribute__.
2. Patched, recording inactive: the common case for requests that never
   enter a caching construct.
3. Patched, recording active: inside a caching block.

Run from the repo root with a tox environment interpreter, eg.:

    .tox/py312-django60/bin/python bin/benchmark.py
"""

import os
import sys
import timeit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ultracache.tests.settings.60")
import django

from django.conf import settings

settings.DATABASES["default"]["NAME"] = ":memory:"

django.setup()

from django.core.management import call_command

call_command("migrate", verbosity=0)

from django.contrib.auth.models import User
from django.db import models

from ultracache import Recorder, clear_recorder, set_recorder
from ultracache.monkey import my__getattribute__

# Create an instance in memory (no DB required for attribute access)
user = User(pk=1, username="testuser")


def access_attributes():
    # Simulate a template or view accessing a few fields
    _ = user.pk
    _ = user.username
    _ = user.is_active
    _ = user.email


def unpatch():
    # django.setup() imported ultracache.monkey which applied the patch to
    # the Model class dict; deleting it restores the original inherited
    # object.__getattribute__.
    if "__getattribute__" in vars(models.Model):
        del models.Model.__getattribute__


def repatch():
    models.Model.__getattribute__ = my__getattribute__


def measure():
    times = timeit.repeat(access_attributes, repeat=5, number=100000)
    return sum(times) / len(times)


def run_baseline():
    unpatch()
    clear_recorder()
    return measure()


def run_patched_inactive():
    repatch()
    clear_recorder()
    return measure()


def run_patched_active():
    repatch()
    set_recorder(Recorder())
    # Warm the ContentType cache so the timing reflects steady state
    access_attributes()
    try:
        return measure()
    finally:
        clear_recorder()


if __name__ == "__main__":
    print("Running benchmark (5 runs of 100,000 iterations, 4 accesses each)...")

    baseline = run_baseline()
    print(f"1. Baseline (Original Django):             {baseline:.4f} seconds")

    patched_inactive = run_patched_inactive()
    print(
        f"2. Patched (No cache session active):      {patched_inactive:.4f} seconds "
        f"({patched_inactive / baseline:.2f}x slower)"
    )

    patched_active = run_patched_active()
    print(
        f"3. Patched (Active cache session):         {patched_active:.4f} seconds "
        f"({patched_active / baseline:.2f}x slower)"
    )

    # Leave the patch in place, matching the state django.setup() produced
    repatch()
