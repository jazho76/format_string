#define _GNU_SOURCE
#include <stdio.h>
#include <unistd.h>

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
    while (vuln())
    {
    }
    return 0;
}
