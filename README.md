# Format String

printf(user_controlled_input). Input gets interpreted as directives, arbitrary reads/writes through stack args.

## Reads

- %p at a controlled slot: leak the raw arg value (a stack qword, or a pointer). Arbitrary read of what's in the slot.
- %s at a controlled slot: deref the arg as a char\* and dump the string there. Arbitrary read at any address.
- %<n>$p / %<n>$s: positional form, target a slot directly.

## Writes

%n family stores chars-printed-so-far through a pointer arg. Pad with %<count>c to hit the target, then fire. Size variants:

- %hhn: 1 byte (low 8 bits of count)
- %hn: 2 bytes (low 16)
- %n: 4 bytes (low 32)
- %lln: 8 bytes (full 64)

Fewer directives with wider writes, but each needs a larger %<count>c, and the count can exceed the buffer or wrap the modulus. Byte/word writes as the practical sweet spot.

Some leak modifiers for reads, same length family: %hhx / %hx / %x / %llx
