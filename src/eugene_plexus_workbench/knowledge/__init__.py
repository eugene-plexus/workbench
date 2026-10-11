"""Reading a person's files (MR2): the text in them, in a child process.

`extract` opens a file and says what it holds (or why it cannot), `worker` is
the short-lived process that runs it, and `reader` starts, times and ends
those processes and keeps what they find beside the original. What a model
is given of any of it is decided elsewhere (`context.py`).
"""
