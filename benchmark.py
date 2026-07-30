import os
import timeit
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ultracache.tests.settings.60")
import django

from django.conf import settings
settings.DATABASES['default']['NAME'] = ':memory:'

django.setup()
from django.core.management import call_command
call_command('migrate', verbosity=0)

from django.db import models
from django.contrib.auth.models import User
from ultracache.monkey import my__getattribute__
from ultracache import _thread_locals

# Create an instance in memory (no DB required for attribute access)
user = User(pk=1, username="testuser")

# Keep a reference to the original C-level __getattribute__
original_getattribute = models.Model.__getattribute__

def access_attributes():
    # Simulate a template or view accessing a few fields
    _ = user.pk
    _ = user.username
    _ = user.is_active
    _ = user.email

def run_baseline():
    models.Model.__getattribute__ = original_getattribute
    times = timeit.repeat(access_attributes, repeat=5, number=100000)
    return sum(times) / len(times)

def run_patched_inactive():
    models.Model.__getattribute__ = my__getattribute__
    if hasattr(_thread_locals, "ultracache_recorder"):
        delattr(_thread_locals, "ultracache_recorder")
    times = timeit.repeat(access_attributes, repeat=5, number=100000)
    return sum(times) / len(times)

def run_patched_active():
    from ultracache.monkey import profile_stats
    for k in profile_stats:
        profile_stats[k] = 0.0
        
    models.Model.__getattribute__ = my__getattribute__
    setattr(_thread_locals, "ultracache_recorder", [])
    times = timeit.repeat(access_attributes, repeat=5, number=100000)
    delattr(_thread_locals, "ultracache_recorder")
    
    print("\nInternals Breakdown (Accumulated across 5 runs of 100,000 iterations):")
    total_tracked = sum(profile_stats.values())
    if total_tracked > 0:
        for k, v in profile_stats.items():
            print(f"  {k:15}: {v:.4f}s ({v/total_tracked*100:.1f}%)")
            
    return sum(times) / len(times)

if __name__ == "__main__":
    print(f"Running benchmark (5 runs of 100,000 iterations, 4 accesses each)...")
    
    baseline = run_baseline()
    print(f"1. Baseline (Original Django):             {baseline:.4f} seconds")

    patched_inactive = run_patched_inactive()
    print(f"2. Patched (No cache session active):      {patched_inactive:.4f} seconds ({patched_inactive/baseline:.2f}x slower)")

    patched_active = run_patched_active()
    print(f"3. Patched (Active cache session):         {patched_active:.4f} seconds ({patched_active/baseline:.2f}x slower)")

    # Clean up the patch just in case
    models.Model.__getattribute__ = original_getattribute
