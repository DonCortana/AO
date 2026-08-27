"""Operator tools — human-in-the-loop surfaces around the measurement pipeline.

Operating System §8 makes human-in-the-loop explicit for v1.0, and Execution
Plan §10 keeps parsing "deliberately assisted manual" until a parser clears
the >=95% labelled-sample gate. These tools are that assistance: they are
built to be reused every client baseline, not for one calibration cycle.
"""
