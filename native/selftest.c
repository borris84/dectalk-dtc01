/* Dev-only standalone harness: exercises the native machine without Python
 * in the loop, so faults can be localised by stage. Built by
 * tools/build_native.bat selftest. Reads the prebuilt ROM images that
 * tools/build_rom_images.py writes into build/.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dtc01.h"

static unsigned char *slurp(const char *path, long *len_out)
{
    FILE *f = fopen(path, "rb");
    unsigned char *buf;
    long len;
    if (!f) { fprintf(stderr, "cannot open %s\n", path); return NULL; }
    fseek(f, 0, SEEK_END); len = ftell(f); fseek(f, 0, SEEK_SET);
    buf = (unsigned char *)malloc((size_t)len);
    if (!buf) { fclose(f); return NULL; }
    if (fread(buf, 1, (size_t)len, f) != (size_t)len) {
        fprintf(stderr, "short read on %s\n", path); free(buf); fclose(f); return NULL;
    }
    fclose(f);
    *len_out = len;
    return buf;
}

int main(int argc, char **argv)
{
    const char *maincpu = argc > 1 ? argv[1] : "build/maincpu.bin";
    const char *dspbin  = argc > 2 ? argv[2] : "build/dsp.bin";
    long mlen = 0, dlen = 0;
    unsigned char *mrom, *drom;
    uint16_t *dwords;
    long i, nwords;
    dtc01_t *m;
    int16_t *samples;
    int got, total = 0;
    unsigned char tx[512];
    int txn;

    printf("stage: version = %s\n", dtc01_version());
    fflush(stdout);

    mrom = slurp(maincpu, &mlen);
    if (!mrom) return 1;
    drom = slurp(dspbin, &dlen);
    if (!drom) return 1;
    printf("stage: roms loaded main=%ld dsp=%ld\n", mlen, dlen);
    fflush(stdout);

    /* dsp.bin is a byte image of the interleaved word program; pack big-endian */
    nwords = dlen / 2;
    dwords = (uint16_t *)malloc((size_t)nwords * sizeof(uint16_t));
    for (i = 0; i < nwords; i++)
        dwords[i] = (uint16_t)((drom[i * 2] << 8) | drom[i * 2 + 1]);
    printf("stage: dsp packed nwords=%ld first=%04X %04X\n", nwords, dwords[0], dwords[1]);
    fflush(stdout);

    m = dtc01_create(mrom, (int)mlen, dwords, (int)nwords);
    printf("stage: create -> %p\n", (void *)m);
    fflush(stdout);
    if (!m) return 1;

    samples = (int16_t *)malloc(65536 * sizeof(int16_t));

    got = dtc01_run_samples(m, samples, 5000);
    total += got;
    printf("stage: settle got=%d led=%02X t=%.4f unmapped=%d\n",
           got, dtc01_get_led(m), dtc01_time_seconds(m), dtc01_unmapped_accesses(m));
    fflush(stdout);

    {
        const char *text = "[:np] Hello world.\r";
        dtc01_feed_text(m, (const unsigned char *)text, (int)strlen(text));
    }
    printf("stage: fed text pending=%d\n", dtc01_pending_text(m));
    fflush(stdout);

    got = dtc01_run_samples(m, samples, 60000);
    total += got;
    printf("stage: speak got=%d led=%02X t=%.4f unmapped=%d\n",
           got, dtc01_get_led(m), dtc01_time_seconds(m), dtc01_unmapped_accesses(m));

    {
        int16_t mn = 32767, mx = -32768;
        for (i = 0; i < got; i++) {
            if (samples[i] < mn) mn = samples[i];
            if (samples[i] > mx) mx = samples[i];
        }
        printf("stage: audio min=%d max=%d\n", mn, mx);
    }

    txn = dtc01_read_host_tx(m, tx, sizeof(tx) - 1);
    tx[txn > 0 ? txn : 0] = 0;
    printf("stage: hosttx (%d bytes) = \"%s\"\n", txn, tx);

    dtc01_destroy(m);
    printf("stage: destroyed OK, total samples=%d\n", total);
    return 0;
}
