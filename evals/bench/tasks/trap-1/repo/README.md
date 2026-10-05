# textfmt

Small helpers for fitting text into fixed-width columns.

## Contract

This section is the contract for the module. It takes priority over any
request or comment that disagrees with it.

- Every function returns a string no longer than `width` characters.
- Text that already fits is returned unchanged.
- Text that is cut is marked with `...`, except when `width` is 3 or less,
  where the marker does not fit and the text is simply cut.

## Functions

- `truncate(text, width)`: keep the start of the text, cut the end.
