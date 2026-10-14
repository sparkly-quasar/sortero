#!/usr/bin/env bash
# Build the small ffmpeg and ffprobe that ship inside Sortero.
#
# Only what Gig prep uses goes in: reading MP3, AAC, ALAC, FLAC, WAV, AIFF,
# Ogg Vorbis and Opus; writing MP3 (LAME) and AIFF; resampling with soxr;
# JPEG and PNG for artwork. A full ffmpeg is over 100 MB; this is a few.
#
#   build/ffmpeg/build.sh [out-dir]
#
# Linux and Windows (an MSYS2 MINGW64 shell) build for the machine they run
# on. macOS builds arm64 and x86_64 and joins them into universal binaries.
# Every download is checked against the SHA-256 below before it's used:
#
#   FFmpeg  https://ffmpeg.org/releases/  (signed with the FFmpeg release key,
#           FCF9 86EA 15E6 E293 A564 4F10 B432 2F04 D676 58D8)
#   LAME    https://lame.sourceforge.io/  (same file as Debian's lame_3.100.orig)
#   soxr    https://sourceforge.net/projects/soxr/  (same as Debian's libsoxr_0.1.3.orig)
#
# The result is GPL (FFmpeg with --enable-gpl, linking LAME and soxr, both
# LGPL). LICENSES and SOURCES.txt go in the out dir, and ship beside the
# binaries.
set -euo pipefail

FFMPEG_VERSION=9.0.2
FFMPEG_SHA256=8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e
LAME_VERSION=3.100
LAME_SHA256=ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e
SOXR_VERSION=0.1.3
SOXR_SHA256=b111c15fdc8c029989330ff559184198c161100a59312f5dc19ddeb9b5a15889

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE/out}"
WORK="${FFMPEG_WORK:-$HERE/work}"
JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"
mkdir -p "$OUT" "$WORK/src"

case "$(uname -s)" in
  Darwin) PLATFORM=mac ;;
  MINGW*|MSYS*) PLATFORM=windows ;;
  *) PLATFORM=linux ;;
esac
EXE=""; [ "$PLATFORM" = windows ] && EXE=".exe"

sha256() {
  if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

fetch() {   # url file sha256
  local dest="$WORK/src/$2"
  [ -f "$dest" ] || curl -fsSL --retry 3 -o "$dest" "$1"
  local got; got="$(sha256 "$dest")"
  if [ "$got" != "$3" ]; then
    echo "$2: SHA-256 is $got, expected $3. Not building from it." >&2
    rm -f "$dest"; exit 1
  fi
}

fetch "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz" \
      "ffmpeg-$FFMPEG_VERSION.tar.xz" "$FFMPEG_SHA256"
fetch "https://downloads.sourceforge.net/project/lame/lame/$LAME_VERSION/lame-$LAME_VERSION.tar.gz" \
      "lame-$LAME_VERSION.tar.gz" "$LAME_SHA256"
fetch "https://downloads.sourceforge.net/project/soxr/soxr-$SOXR_VERSION-Source.tar.xz" \
      "soxr-$SOXR_VERSION-Source.tar.xz" "$SOXR_SHA256"

# What goes into ffmpeg: anything not listed is left out.
COMPONENTS=(
  --enable-protocol=file,pipe
  --enable-demuxer=mp3,aac,mov,wav,aiff,flac,ogg,image2,image2pipe,image_jpeg_pipe,image_png_pipe
  --enable-decoder=mp3,mp3float,aac,alac,flac,vorbis,opus,mjpeg,png
  --enable-decoder=pcm_u8,pcm_s8,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s24be,pcm_s32le,pcm_s32be
  --enable-decoder=pcm_f32le,pcm_f32be,pcm_f64le,pcm_f64be
  --enable-encoder=libmp3lame,pcm_s16be,pcm_s16le,mjpeg   # pcm_s16le: the decode test's null output
  --enable-muxer=mp3,aiff,image2,null
  --enable-parser=mpegaudio,aac,flac,mjpeg,png,vorbis,opus
  --enable-filter=aresample,aformat,anull,scale,format,null
)

build_arch() {   # arch prefix -> builds lame, soxr and ffmpeg into prefix
  local arch="$1" prefix="$2" cc_flags="" host="" cross=() cmake_arch=()
  if [ "$PLATFORM" = mac ]; then
    cc_flags="-arch $arch -mmacosx-version-min=11.0"
    cmake_arch=(-DCMAKE_OSX_ARCHITECTURES="$arch" -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0)
    if [ "$arch" != "$(uname -m)" ]; then
      host="--host=${arch/arm64/aarch64}-apple-darwin"
      cross=(--enable-cross-compile --arch="$arch" --target-os=darwin --disable-x86asm)
    fi
  fi
  mkdir -p "$prefix"
  local b="$WORK/build-$arch"
  rm -rf "$b" && mkdir -p "$b"

  # LAME, static. Its symbol list names a function 3.100 doesn't have, which
  # breaks linking on macOS; dropping the line is the usual fix.
  tar -xzf "$WORK/src/lame-$LAME_VERSION.tar.gz" -C "$b"
  ( cd "$b/lame-$LAME_VERSION"
    sed -i.bak '/lame_init_old/d' include/libmp3lame.sym
    CFLAGS="-O2 $cc_flags" ./configure --prefix="$prefix" $host --disable-shared \
      --enable-static --disable-frontend --disable-decoder --disable-dependency-tracking
    make -j"$JOBS" && make install )

  # soxr, static, without OpenMP (one fewer runtime to ship).
  tar -xJf "$WORK/src/soxr-$SOXR_VERSION-Source.tar.xz" -C "$b"
  local generator=(); [ "$PLATFORM" = windows ] && generator=(-G "MSYS Makefiles")
  cmake -S "$b/soxr-$SOXR_VERSION-Source" -B "$b/soxr-build" "${generator[@]}" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" "${cmake_arch[@]}" \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DBUILD_SHARED_LIBS=OFF -DWITH_OPENMP=OFF \
    -DBUILD_TESTS=OFF -DBUILD_EXAMPLES=OFF -DWITH_LSR_BINDINGS=OFF
  cmake --build "$b/soxr-build" -j "$JOBS" && cmake --install "$b/soxr-build"

  tar -xJf "$WORK/src/ffmpeg-$FFMPEG_VERSION.tar.xz" -C "$b"
  local ldextra="" ; [ "$PLATFORM" = windows ] && ldextra="-static"
  ( cd "$b/ffmpeg-$FFMPEG_VERSION"
    ./configure --prefix="$prefix" "${cross[@]}" \
      --cc="${CC:-cc}" --extra-cflags="-I$prefix/include $cc_flags" \
      --extra-ldflags="-L$prefix/lib $cc_flags $ldextra" --extra-libs="-lm" \
      --enable-gpl --enable-static --disable-shared --disable-autodetect --enable-zlib \
      --disable-everything --disable-doc --disable-network --disable-debug \
      --disable-ffplay --enable-ffmpeg --enable-ffprobe \
      --enable-libmp3lame --enable-libsoxr --enable-swresample --enable-swscale \
      "${COMPONENTS[@]}"
    make -j"$JOBS" && make install )
}

if [ "$PLATFORM" = mac ]; then
  for arch in arm64 x86_64; do build_arch "$arch" "$WORK/prefix-$arch"; done
  for tool in ffmpeg ffprobe; do
    lipo -create "$WORK/prefix-arm64/bin/$tool" "$WORK/prefix-x86_64/bin/$tool" \
         -output "$OUT/$tool"
  done
else
  arch="$(uname -m)"
  build_arch "$arch" "$WORK/prefix-$arch"
  cp "$WORK/prefix-$arch/bin/ffmpeg$EXE" "$WORK/prefix-$arch/bin/ffprobe$EXE" "$OUT/"
fi
strip "$OUT/ffmpeg$EXE" "$OUT/ffprobe$EXE" 2>/dev/null || true

# Licences and where the source came from: the GPL asks for both.
b="$(ls -d "$WORK"/build-* | head -1)"
mkdir -p "$OUT/LICENSES"
cp "$b/ffmpeg-$FFMPEG_VERSION/COPYING.GPLv3" "$OUT/LICENSES/FFmpeg-GPLv3.txt"
cp "$b/ffmpeg-$FFMPEG_VERSION/LICENSE.md" "$OUT/LICENSES/FFmpeg-LICENSE.md"
cp "$b/lame-$LAME_VERSION/COPYING" "$OUT/LICENSES/LAME-LGPL.txt"
cp "$b/soxr-$SOXR_VERSION-Source/LICENCE" "$OUT/LICENSES/soxr-LGPL.txt"
cat > "$OUT/SOURCES.txt" <<EOF
The ffmpeg and ffprobe programs beside this file were built from these
sources, unmodified apart from one line removed from LAME's symbol list
(lame_init_old, which LAME 3.100 doesn't define), by Sortero's
build/ffmpeg/build.sh, which also lists every configure option used:

  FFmpeg $FFMPEG_VERSION  https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz
                sha256 $FFMPEG_SHA256
  LAME $LAME_VERSION     https://downloads.sourceforge.net/project/lame/lame/$LAME_VERSION/lame-$LAME_VERSION.tar.gz
                sha256 $LAME_SHA256
  soxr $SOXR_VERSION     https://downloads.sourceforge.net/project/soxr/soxr-$SOXR_VERSION-Source.tar.xz
                sha256 $SOXR_SHA256

Sortero's source, including that script: https://github.com/sparkly-quasar/sortero
EOF
echo "Built $("$OUT/ffmpeg$EXE" -hide_banner -version | head -1) into $OUT"
