"""Golden tests: documents this engine produces, held to the bytes.

A golden here is not a file checked in beside the test but
the specification itself: doc/printout.md ends with a printout,
and :mod:`tests.golden.test_printout_example` builds the template
that example names and compares what comes out.  A specification
whose examples are generated cannot drift from the engine without
one of them noticing.

"""
