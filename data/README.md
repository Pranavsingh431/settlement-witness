# Data

`fixtures/` holds small committed files. They are inputs to tests and examples, they are reviewed
like code, and they never contain real merchant or personal data.

`generated/` holds datasets and run output produced on your machine. Git ignores everything in it
apart from the placeholder file. Never commit generated output. It is reproduced from a seed and a
version, and a committed copy would go stale and start disagreeing with the code that made it.
