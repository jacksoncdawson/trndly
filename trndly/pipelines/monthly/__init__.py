"""Monthly tick: scrape → aggregate → features → train → evaluate → predict.

Each stage is an importable module exposing ``run_<stage>()``. The shared
CLI ``python -m pipelines.monthly`` (see ``cli.py``) drives the full chain
or any individual stage.
"""
