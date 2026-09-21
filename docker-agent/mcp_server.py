from fastmcp import FastMCP
import subprocess
import shlex


mcp = FastMCP("Docker MCP Server")



def run_docker_command(command):
    """Run a Docker CLI command and return stdout/stderr."""
    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        return f"Error: {result.stderr.strip()}"

    return result.stdout.strip()


@mcp.tool
def show_running_containers():
    """Show currently running Docker containers."""
    return run_docker_command(["docker", "ps"])


@mcp.tool
def show_all_containers():
    """Show all Docker containers, including stopped containers."""
    return run_docker_command(["docker", "ps", "-a"])


@mcp.tool
def show_container_logs(container_name: str):
    """Show the last 50 log lines of a specified container."""
    return run_docker_command(
        ["docker", "logs", "--tail", "50", container_name]
    )


@mcp.tool
def inspect_container(container_name: str):
    """Show detailed configuration and runtime information for a container."""
    return run_docker_command(
        ["docker", "inspect", container_name]
    )


@mcp.tool
def show_container_stats():
    """Show CPU, memory, network and process statistics for running containers."""
    return run_docker_command(
        ["docker", "stats", "--no-stream"]
    )


@mcp.tool
def show_docker_images():
    """Show all Docker images available locally."""
    return run_docker_command(
        ["docker", "images"]
    )


@mcp.tool
def show_docker_networks():
    """Show Docker networks."""
    return run_docker_command(
        ["docker", "network", "ls"]
    )


@mcp.tool
def show_docker_volumes():
    """Show Docker volumes."""
    return run_docker_command(
        ["docker", "volume", "ls"]
    )


@mcp.tool
def restart_container(container_name: str):
    """Restart a specified Docker container."""
    return run_docker_command(
        ["docker", "restart", container_name]
    )


@mcp.tool
def stop_container(container_name: str):
    """Stop a specified Docker container."""
    return run_docker_command(
        ["docker", "stop", container_name]
    )


@mcp.tool
def start_container(container_name: str):
    """Start a stopped Docker container."""
    return run_docker_command(
        ["docker", "start", container_name]
    )


@mcp.tool
def show_container_processes(container_name: str):
    """Show processes currently running inside a container."""
    return run_docker_command(
        ["docker", "top", container_name]
    )


if __name__ == "__main__":
    mcp.run()
