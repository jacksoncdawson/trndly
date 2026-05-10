"""Allow ``python -m pipelines.monthly <subcommand>`` to invoke the CLI."""
from pipelines.monthly.cli import main


if __name__ == "__main__":
    main()
