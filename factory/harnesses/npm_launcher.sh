#!/bin/sh
# npm_launcher.sh PREFIX NAME
#
# Run in a harness's build stage, after `npm install -g --prefix PREFIX ...`.
# Writes /opt/harness/bin/NAME: the one path a route's session command names.
#
# The launcher cannot rely on anything the unit's image has, because that image
# is the project's. So:
#
# - A command that is a script (`#!/usr/bin/env node`) is run with the stage's
#   own Node, copied to PREFIX/bin/node. Left to its shebang it would find the
#   project's Node, or none -- and PATH is not changed to make it find this one,
#   because the agent's own checks inherit PATH and must run on the project's.
# - A command that is a binary is run as it is.
# - The stage's certificate bundle travels too, and is used only when the image
#   has none of its own. A stock slim image ships none; a tool that reads the
#   system's store then fails every TLS handshake and retries until the timeout.
#   An image that has its own -- a company's CA included -- keeps it.
set -eu

prefix=$1
name=$2
target=$(readlink -f "$prefix/bin/$name")
[ -f "$target" ] || { echo "npm_launcher: $prefix/bin/$name is not installed" >&2; exit 1; }

mkdir -p /opt/harness/bin
if [ -r /etc/ssl/certs/ca-certificates.crt ]; then
  mkdir -p /opt/harness/certs
  cp /etc/ssl/certs/ca-certificates.crt /opt/harness/certs/ca-certificates.crt
fi

if [ "$(head -c 2 "$target")" = "#!" ]; then
  cp "$(command -v node)" "$prefix/bin/node"
  run="$prefix/bin/node $target"
else
  run="$target"
fi

cat > "/opt/harness/bin/$name" <<EOF
#!/bin/sh
if [ -z "\${SSL_CERT_FILE:-}" ] && [ ! -r /etc/ssl/certs/ca-certificates.crt ] \\
   && [ -r /opt/harness/certs/ca-certificates.crt ]; then
  export SSL_CERT_FILE=/opt/harness/certs/ca-certificates.crt
fi
exec $run "\$@"
EOF
chmod 0755 "/opt/harness/bin/$name"
