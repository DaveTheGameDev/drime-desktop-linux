#!/bin/sh
# Installed as /app/bin/fusermount3. rclone (bazil.org/fuse) execs fusermount3 with the
# FUSE communication socket in _FUSE_COMMFD; this forwards that fd and the call to the
# host's fusermount3 through flatpak-spawn --host, so the host opens /dev/fuse and the
# mount is created in the host's mount namespace (visible to every app), while rclone's
# FUSE server keeps running in the sandbox. Same script as Pika Backup / Deja Dup.

if [ -z "$_FUSE_COMMFD" ]; then
    FD_ARGS=
else
    FD_ARGS="--env=_FUSE_COMMFD=${_FUSE_COMMFD} --forward-fd=${_FUSE_COMMFD}"
fi

if [ -e /proc/self/fd/3 ] && [ 3 != "$_FUSE_COMMFD" ]; then
    FD_ARGS="$FD_ARGS --forward-fd=3"
fi

# If the fusermount3 binary doesn't exist we try fusermount
# command -v will return 0 if the command exists and 127 otherwise
if flatpak-spawn --host sh -c "command -v fusermount3" > /dev/null; then
    exec flatpak-spawn --host --forward-fd=1 --forward-fd=2 $FD_ARGS fusermount3 "$@"
else
    exec flatpak-spawn --host --forward-fd=1 --forward-fd=2 $FD_ARGS fusermount "$@"
fi
