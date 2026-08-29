#!/bin/bash
# ============================================================================
# PSORTb Local Check & Install Script
# Checks if PSORTb is installed, if not downloads and installs it
# ============================================================================

PSORTB_ROOT="/usr/local/psortb"
PSORTB_BIN="/usr/local/bin/psortb"

echo "============================================"
echo "PSORTb Installation Check"
echo "============================================"
echo ""

# Function to check if psortb works
check_psortb() {
    if [ -f "$PSORTB_BIN" ]; then
        # Test if it actually runs
        if $PSORTB_BIN --version 2>/dev/null | grep -qi "psort"; then
            echo "[OK] PSORTb binary exists and runs"
            return 0
        fi
    fi
    
    if [ -f "$PSORTB_ROOT/bin/psort" ]; then
        if $PSORTB_ROOT/bin/psort --version 2>/dev/null | grep -qi "psort"; then
            echo "[OK] PSORTb found at $PSORTB_ROOT/bin/psort"
            # Create symlink if missing
            if [ ! -f "$PSORTB_BIN" ]; then
                ln -sf "$PSORTB_ROOT/bin/psort" "$PSORTB_BIN"
                echo "[OK] Created symlink at $PSORTB_BIN"
            fi
            return 0
        fi
    fi
    
    return 1
}

# Check if already installed
echo "Checking for existing PSORTb installation..."
if check_psortb; then
    echo ""
    echo "PSORTb is already installed!"
    $PSORTB_BIN --version 2>/dev/null || echo "(version check not available)"
    echo ""
    echo "Binary: $PSORTB_BIN"
    echo "Root:   $PSORTB_ROOT"
    exit 0
fi

echo "[WARN] PSORTb not found or not working"
echo ""
echo "============================================"
echo "Installing PSORTb from source..."
echo "============================================"
echo ""

# Install system dependencies
echo "[1/6] Installing system dependencies..."
# Disable broken Colab apt source
rm -f /etc/apt/sources.list.d/r2u.list 2>/dev/null || true
apt-get update -qq 2>/dev/null
apt-get install -y -qq --no-install-recommends bioperl hmmer prodigal perl libstring-perl wget 2>/dev/null || {
    echo "  Trying with sudo..."
    sudo apt-get update -qq 2>/dev/null
    sudo apt-get install -y -qq --no-install-recommends bioperl hmmer prodigal perl libstring-perl wget 2>/dev/null
}

# Create directories
mkdir -p $PSORTB_ROOT 2>/dev/null || sudo mkdir -p $PSORTB_ROOT
cd /tmp

# 2. Download and install pftools
echo "[2/6] Installing pftools..."
if ! command -v pfscan &> /dev/null; then
    wget -q https://github.com/sib-swiss/pftools3/releases/download/v3.2.14/pftools-3.2.14-linux-x86_64.tar.gz
    tar xzf pftools-3.2.14-linux-x86_64.tar.gz
    cp pftools-3.2.14-linux-x86_64/bin/* /usr/local/bin/ 2>/dev/null || sudo cp pftools-3.2.14-linux-x86_64/bin/* /usr/local/bin/
    cp pftools-3.2.14-linux-x86_64/lib/* /usr/local/lib/ 2>/dev/null || sudo cp pftools-3.2.14-linux-x86_64/lib/* /usr/local/lib/
    ldconfig 2>/dev/null || sudo ldconfig
    echo "  [OK] pftools installed"
else
    echo "  [OK] pftools already present"
fi

# 3. Download and install libpsortb
echo "[3/6] Installing libpsortb..."
if [ ! -f "$PSORTB_ROOT/lib/libpsortb.so" ] && [ ! -f "/usr/local/lib/libpsortb.so" ]; then
    wget -q https://psort.org/download/libpsortb-1.0.tar.gz
    tar xzf libpsortb-1.0.tar.gz
    cd libpsortb-1.0
    ./configure --prefix=$PSORTB_ROOT 2>/dev/null
    make 2>/dev/null
    make install 2>/dev/null || sudo make install
    cd /tmp
    echo "  [OK] libpsortb installed"
else
    echo "  [OK] libpsortb already present"
fi

# 4. Download and install PSORTb Perl module
echo "[4/6] Installing PSORTb Perl module..."
if [ ! -d "$PSORTB_ROOT/lib/perl5/Bio/Tools/PSort" ]; then
    wget -q https://psort.org/download/bio-tools-psort-all.3.0.6.tar.gz
    tar xzf bio-tools-psort-all.3.0.6.tar.gz
    cd bio-tools-psort-all
    
    # Get Docker-specific standalone files
    wget -q https://psort.org/download/docker/psortb_standalone_for_docker.tar.gz
    tar xzf psortb_standalone_for_docker.tar.gz
    cp psortb_standalone_for_docker/Makefile.PL ./
    
    # Get Docker defaults
    wget -q https://psort.org/download/docker/psortb.defaults -O psortb.defaults 2>/dev/null || true
    
    # Install
    perl Makefile.PL INSTALL_BASE=$PSORTB_ROOT 2>/dev/null
    make 2>/dev/null
    make install 2>/dev/null || sudo make install
    cd /tmp
    echo "  [OK] PSORTb Perl module installed"
else
    echo "  [OK] PSORTb Perl module already present"
fi

# 5. Set up the psortb binary
echo "[5/6] Setting up psortb binary..."
mkdir -p $PSORTB_ROOT/bin

# Copy the standalone bin files if available
if [ -d "/tmp/bio-tools-psort-all/psortb_standalone_for_docker/bin" ]; then
    cp -r /tmp/bio-tools-psort-all/psortb_standalone_for_docker/bin/* $PSORTB_ROOT/bin/ 2>/dev/null || sudo cp -r /tmp/bio-tools-psort-all/psortb_standalone_for_docker/bin/* $PSORTB_ROOT/bin/
fi

# Create wrapper script
cat > $PSORTB_ROOT/bin/psort << 'WRAPPER'
#!/bin/bash
export PSORT_ROOT=/usr/local/psortb
export LD_LIBRARY_PATH=/usr/local/psortb/lib:$LD_LIBRARY_PATH
perl /usr/local/psortb/lib/perl5/Bio/Tools/PSort/psort "$@"
WRAPPER
chmod +x $PSORTB_ROOT/bin/psort

# Create global symlink
cat > /usr/local/bin/psortb << 'EOF'
#!/bin/bash
export PSORT_ROOT=/usr/local/psortb
export LD_LIBRARY_PATH=/usr/local/psortb/lib:$LD_LIBRARY_PATH
perl $PSORTB_ROOT/lib/perl5/Bio/Tools/PSort/psort "$@"
EOF
chmod +x /usr/local/bin/psortb
ln -sf /usr/local/bin/psortb /usr/local/bin/psort

# 6. Verify
echo "[6/6] Verifying installation..."
if check_psortb; then
    echo ""
    echo "============================================"
    echo "PSORTb installation complete!"
    echo "============================================"
    echo ""
    echo "Binary: $PSORTB_BIN"
    echo "Root:   $PSORTB_ROOT"
    echo ""
    echo "Usage:"
    echo "  psortb -i input.fasta -p --output terse"
    echo "  (Use -p for Gram+, -n for Gram-, -a for Archaea)"
else
    echo ""
    echo "[ERROR] PSORTb installation failed"
    echo "Check the output above for errors"
    exit 1
fi

# Cleanup
rm -rf /tmp/pftools* /tmp/libpsortb* /tmp/bio-tools-psort-all* /tmp/psortb*
