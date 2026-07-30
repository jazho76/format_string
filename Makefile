CC      := gcc
CFLAGS  := -O0 -fPIE \
           -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 \
           -fcf-protection=none -fstack-protector-strong
LDFLAGS := -pie -Wl,-z,relro -Wl,-z,now
BIN     := vuln

.PHONY: build clean
build: $(BIN)

$(BIN): src/vuln.c
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $<

clean:
	rm -f $(BIN) *.o
