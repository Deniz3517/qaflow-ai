# Okey Turkano — Robot Framework suite

## Layout
- `resources/` — Robot resource files holding shared keywords and locators.
- `tests/` — Robot test suites consuming those resources.

## Run
```bash
robot --variable BROWSER:Chrome tests/
```
