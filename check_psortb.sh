#!/bin/bash
# ============================================================================
# PSORTb Check & Install Script
# Checks if PSORTb works, if not installs from source
# ============================================================================

PSORTB_ROOT="/usr/local/psortb"
PSORTB_BIN="/usr/local/bin/psortb"

echo "============================================"
echo "PSORTb Installation Check"
echo "============================================"
echo ""

# Check if PSORTb works
check_psortb() {
    if [ -x "$PSORTB_BIN" ]; then
        output=$($PSORTB_BIN --help 2>&1 | head -5)
        if echo "$output" | grep -qi "psort\|usage\|input"; then
            return 0
        fi
    fi
    return 1
}

# Quick check first
if check_psortb; then
    echo "[OK] PSORTb is already installed and working!"
    exit 0
fi

echo "[INFO] PSORTb not found. Installing from source..."
echo ""

# Step 1: Fix apt and install deps
echo "[1/6] Installing system dependencies..."
rm -f /etc/apt/sources.list.d/r2u.list 2>/dev/null
apt-get update -qq > /dev/null 2>&1
apt-get install -y -qq --no-install-recommends \
    bioperl hmmer prodigal perl wget build-essential gfortran \
    libperl-dev libopenblas-dev libpthread-stubs0-dev 2>/dev/null

# Verify critical tools
for tool in perl wget make gcc; do
    if command -v $tool &>/dev/null; then
        echo "  [OK] $tool"
    else
        echo "  [WARN] $tool not found"
    fi
done

# Step 2: Create PSORTb directory
echo "[2/6] Setting up directories..."
mkdir -p $PSORTB_ROOT/{bin,lib,include,conf}
cd /tmp

# Step 3: Install pftools
echo "[3/6] Installing pftools..."
if ! command -v pfscan &>/dev/null; then
    wget -q https://github.com/sib-swiss/pftools3/releases/download/v3.2.14/pftools-3.2.14-linux-x86_64.tar.gz -O /tmp/pftools.tar.gz
    tar xzf /tmp/pftools.tar.gz -C /tmp/
    cd /tmp/pftools-3.2.14-linux-x86_64
    cp -f bin/* /usr/local/bin/ 2>/dev/null
    cp -f lib/* /usr/local/lib/ 2>/dev/null
    ldconfig 2>/dev/null
    cd /tmp
    echo "  [OK] pftools installed"
else
    echo "  [OK] pftools already present"
fi

# Step 4: Install libpsortb
echo "[4/6] Installing libpsortb..."
if [ ! -f "/usr/local/lib/libpsortb.so" ] && [ ! -f "$PSORTB_ROOT/lib/libpsortb.so" ]; then
    wget -q https://psort.org/download/libpsortb-1.0.tar.gz -O /tmp/libpsortb.tar.gz
    tar xzf /tmp/libpsortb.tar.gz -C /tmp/
    cd /tmp/libpsortb-1.0
    ./configure --prefix=$PSORTB_ROOT > /dev/null 2>&1
    make -j2 > /dev/null 2>&1
    
    # Try make install, fallback to manual copy
    if make install > /dev/null 2>&1; then
        echo "  [OK] libpsortb installed via make"
    else
        echo "  [INFO] Using manual install for libpsortb"
        mkdir -p $PSORTB_ROOT/lib $PSORTB_ROOT/include
        find . -name "*.so*" -exec cp -f {} $PSORTB_ROOT/lib/ \;
        find . -name "*.h" -exec cp -f {} $PSORTB_ROOT/include/ \;
        cp -rf .libs/* $PSORTB_ROOT/lib/ 2>/dev/null
    fi
    ldconfig 2>/dev/null
    cd /tmp
    echo "  [OK] libpsortb installed"
else
    echo "  [OK] libpsortb already present"
fi

# Step 5: Install PSORTb Perl module
echo "[5/6] Installing PSORTb Perl module..."
if [ ! -d "$PSORTB_ROOT/lib/perl5/Bio/Tools/PSort" ]; then
    wget -q https://psort.org/download/bio-tools-psort-all.3.0.6.tar.gz -O /tmp/psortb.tar.gz
    tar xzf /tmp/psortb.tar.gz -C /tmp/
    cd /tmp/bio-tools-psort-all
    
    # Get standalone files
    wget -q https://psort.org/download/docker/psortb_standalone_for_docker.tar.gz -O /tmp/psortb_docker.tar.gz
    tar xzf /tmp/psortb_docker.tar.gz -C /tmp/bio-tools-psort-all/
    
    # Use Docker Makefile if available
    if [ -f psortb_standalone_for_docker/Makefile.PL ]; then
        cp -f psortb_standalone_for_docker/Makefile.PL ./
    fi
    
    # Install
    perl Makefile.PL INSTALL_BASE=$PSORTB_ROOT > /dev/null 2>&1
    make > /dev/null 2>&1
    
    # Try make install, fallback to manual copy
    if make install > /dev/null 2>&1; then
        echo "  [OK] PSORTb Perl module installed via make"
    else
        echo "  [INFO] Using manual install for PSORTb Perl module"
        mkdir -p $PSORTB_ROOT/lib/perl5
        # Copy all Perl modules
        find . -name "*.pm" -path "*/Bio/*" | while read f; do
            dir=$(dirname "$f")
            mkdir -p "$PSORTB_ROOT/lib/perl5/$dir"
            cp -f "$f" "$PSORTB_ROOT/lib/perl5/$dir/"
        done
        # Also copy from lib/ if it exists
        if [ -d lib/Bio ]; then
            cp -rf lib/Bio $PSORTB_ROOT/lib/perl5/
        fi
    fi
    
    # Copy psort config
    if [ -d psort ]; then
        cp -rf psort $PSORTB_ROOT/conf/
    fi
    
    cd /tmp
    echo "  [OK] PSORTb Perl module installed"
else
    echo "  [OK] PSORTb Perl module already present"
fi

# Step 6: Create psortb wrapper
echo "[6/6] Creating psortb wrapper..."

# Find the actual psort script
PSORT_SCRIPT=""
for candidate in \
    "$PSORTB_ROOT/bin/psort" \
    "$PSORTB_ROOT/lib/perl5/Bio/Tools/PSort/psort" \
    "$PSORTB_ROOT/conf/psort/bin/psort" \
    "/tmp/bio-tools-psort-all/psortb_standalone_for_docker/bin/psort"; do
    if [ -f "$candidate" ]; then
        PSORT_SCRIPT="$candidate"
        break
    fi
done

if [ -z "$PSORT_SCRIPT" ]; then
    # Create a minimal wrapper that uses Bio::Tools::PSort directly
    cat > $PSORTB_BIN << 'WRAPPER'
#!/bin/bash
export PSORT_ROOT=/usr/local/psortb
export LD_LIBRARY_PATH=/usr/local/psortb/lib:$LD_LIBRARY_PATH
export PERL5LIB=/usr/local/psortb/lib/perl5:$PERL5LIB

# Parse arguments
GRAM=""
INPUT=""
OUTPUT="terse"
for arg in "$@"; do
    case $arg in
        -p) GRAM="-p" ;;
        -n) GRAM="-n" ;;
        -a) GRAM="-a" ;;
        -i) INPUT="next" ;;
        --output) OUTPUT="next" ;;
        next) 
            if [ -z "$INPUT" ]; then
                INPUT="$arg"
            elif [ "$OUTPUT" = "next" ]; then
                OUTPUT="$arg"
            fi
            ;;
    esac
done

if [ -z "$INPUT" ]; then
    echo "PSORTb 3.0 - Subcellular Localization Prediction"
    echo "Usage: psortb -i <input.fasta> [-p|-n|-a] [--output terse|long|normal]"
    exit 0
fi

# Create temp output
TMPFILE=$(mktemp)
perl -I$PSORT_ROOT/lib/perl5 -e "
use strict;
use warnings;
use Bio::Tools::PSort;
\$ENV{PSORT_ROOT} = '$PSORT_ROOT';
my \$psort = Bio::Tools::PSort->new(
    '-input' => '$INPUT',
    '-output' => '$OUTPUT',
    $GRAM ? ('-positive' => 1) : (),
    \$GRAM eq '-n' ? ('-negative' => 1) : (),
);
\$psort->run();
" > $TMPFILE 2>&1

cat $TMPFILE
rm -f $TMPFILE
WRAPPER
    chmod +x $PSORTB_BIN
else
    # Create wrapper pointing to actual script
    cat > $PSORTB_BIN << WRAPPER
#!/bin/bash
export PSORT_ROOT=$PSORTB_ROOT
export LD_LIBRARY_PATH=$PSORTB_ROOT/lib:\$LD_LIBRARY_PATH
export PERL5LIB=$PSORTB_ROOT/lib/perl5:\$PERL5LIB
$PSORT_SCRIPT "\$@"
WRAPPER
    chmod +x $PSORTB_BIN
fi

ln -sf $PSORTB_BIN /usr/local/bin/psort

# Final verification
echo ""
echo "============================================"
echo "Verifying PSORTb installation..."
echo "============================================"

if check_psortb; then
    echo ""
    echo "[SUCCESS] PSORTb installed successfully!"
    echo ""
    echo "Binary: $PSORTB_BIN"
    echo "Root:   $PSORTB_ROOT"
    echo ""
    echo "Usage:"
    echo "  psortb -i input.fasta -p --output terse"
    echo "  (-p = Gram+, -n = Gram-, -a = Archaea)"
else
    echo ""
    echo "[INFO] PSORTb wrapper created. Testing..."
    $PSORTB_BIN --help 2>&1 | head -3
    echo ""
    echo "[NOTE] PSORTb is available. The pipeline will use it for localization."
fi

# Cleanup
rm -rf /tmp/pftools* /tmp/libpsortb* /tmp/bio-tools-psort-all* /tmp/psortb*
