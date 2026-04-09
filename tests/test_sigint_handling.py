"""Test SIGINT handling in web terminal."""

import subprocess
import time
import signal
import sys


def test_sigint_cleanup():
    """Test that ^C exits cleanly without traceback spam."""
    # Start the web terminal
    proc = subprocess.Popen(
        [sys.executable, "-m", "nemo_oo_agents_cli", "oo", "term", "--port", "8001"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    
    # Give it a moment to start up
    time.sleep(2)
    
    # Send SIGINT (simulate ^C)
    proc.send_signal(signal.SIGINT)
    
    # Wait for it to exit
    stdout, stderr = proc.communicate(timeout=5)
    
    # Check that it exited cleanly
    assert proc.returncode == 0, f"Expected exit code 0, got {proc.returncode}"
    
    # Check that there's no ugly traceback in stderr
    assert "Traceback" not in stderr, f"Found traceback in stderr: {stderr}"
    assert "CancelledError" not in stderr, f"Found CancelledError in stderr: {stderr}"
    
    print("✓ Terminal exits cleanly on SIGINT")


if __name__ == "__main__":
    test_sigint_cleanup()

