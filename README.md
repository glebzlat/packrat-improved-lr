# Toy Packrat parser with improved left recursion support

The original idea for left recursion handling was taken from "Improved Packrat
Parser Left Recursion Support" by James R. Douglass.

I removed the grow rule creation from the parser, assuming that rule chains
will be constructed by the generator.
