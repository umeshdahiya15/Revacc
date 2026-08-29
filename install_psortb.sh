#!/bin/bash
# ============================================================================
# PSORTb Standalone Installer for Google Colab
# Installs PSORTb without Docker
# ============================================================================

set -e

PSORTB_ROOT="/usr/local/psortb"
echo "Installing PSORTb to $PSORTB_ROOT ..."

# Install system dependencies
apt-get update -qq
apt-get install -y -qq bioperl hmmer prodigal perl libstring-perl

# Create directories
mkdir -p $PSORTB_ROOT
cd /tmp

# 1. Download and install pftools (required by PSORTb)
echo "[1/5] Installing pftools..."
wget -q https://github.com/sib-swiss/pftools3/releases/download/v3.2.14/pftools-3.2.14-linux-x86_64.tar.gz
tar xzf pftools-3.2.14-linux-x86_64.tar.gz
cp pftools-3.2.14-linux-x86_64/bin/* /usr/local/bin/ 2>/dev/null || true
cp pftools-3.2.14-linux-x86_64/lib/* /usr/local/lib/ 2>/dev/null || true
ldconfig 2>/dev/null || true

# 2. Download and install libpsortb
echo "[2/5] Installing libpsortb..."
wget -q https://psort.org/download/libpsortb-1.0.tar.gz
tar xzf libpsortb-1.0.tar.gz
cd libpsortb-1.0
./configure --prefix=$PSORTB_ROOT
make
make install
cd /tmp

# 3. Download and install PSORTb Perl module
echo "[3/5] Installing PSORTb Perl module..."
wget -q https://psort.org/download/bio-tools-psort-all.3.0.6.tar.gz
tar xzf bio-tools-psort-all.3.0.6.tar.gz
cd bio-tools-psort-all

# Get Docker-specific standalone files
wget -q https://psort.org/download/docker/psortb_standalone_for_docker.tar.gz
tar xzf psortb_standalone_for_docker.tar.gz
cp psortb_standalone_for_docker/Makefile.PL ./

# Get Docker defaults
wget -q https://psort.org/download/docker/psortb.defaults -O psortb.defaults

# Install
perl Makefile.PL INSTALL_BASE=$PSORTB_ROOT
make
make install
cd /tmp

# 4. Set up the psortb binary
echo "[4/5] Setting up psortb binary..."
mkdir -p $PSORTB_ROOT/bin

# Copy the standalone bin files
if [ -d "bio-tools-psort-all/psortb_standalone_for_docker/bin" ]; then
    cp -r bio-tools-psort-all/psortb_standalone_for_docker/bin/* $PSORTB_ROOT/bin/
fi

# Create wrapper script
cat > $PSORTB_ROOT/bin/psort << 'WRAPPER'
#!/bin/bash
export PSORT_ROOT=/usr/local/psortb
export LD_LIBRARY_PATH=/usr/local/psortb/lib:$LD_LIBRARY_PATH
perl /usr/local/psortb/lib/perl5/Bio/Tools/PSort/psort @ARGS
WRAPPER
chmod +x $PSORTB_ROOT/bin/psort

# Also create a simple wrapper that the Python code can find
cat > /usr/local/bin/psortb << 'EOF'
#!/bin/bash
export PSORT_ROOT=/usr/local/psortb
export LD_LIBRARY_PATH=/usr/local/psortb/lib:$LD_LIBRARY_PATH
perl $PSORTB_ROOT/lib/perl5/Bio/Tools/PSort/psort "$@"
EOF
chmod +x /usr/local/bin/psortb
ln -sf /usr/local/bin/psortb /usr/local/bin/psort

# 5. Verify
echo "[5/5] Verifying installation..."
if [ -f "$PSORTB_ROOT/bin/psort" ] || [ -f "/usr/local/bin/psortb" ]; then
    echo ""
    echo "============================================"
    echo "PSORTb installed successfully!"
    echo "============================================"
    echo "Binary: /usr/local/bin/psortb"
    echo "Root:   $PSORTB_ROOT"
    echo ""
    echo "Test with:"
    echo "  psortb -i input.fasta -p --output terse"
else
    echo "[WARN] PSORTb installation may be incomplete"
fi

# Cleanup
rm -rf /tmp/pftools* /tmp/libpsortb* /tmp/bio-tools-psort-all* /tmp/psortb*
