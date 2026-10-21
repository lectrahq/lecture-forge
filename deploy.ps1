# Copies the project from this Windows PC to the VPS (over scp) and rebuilds
# the Docker containers there.
#
#   .\deploy.ps1                      # copy everything, rebuild changed services
#   .\deploy.ps1 -Service backend     # copy everything, rebuild only the backend
#
# Secrets (.env files) and the data/ folder are never copied: they live only on the VPS.

param(
    [string]$Server = "lecture-vps",             # host alias from ~/.ssh/config (holds the real host, user and port)
    [string]$RemoteDir = "/opt/docker/lecture-transcriber",  # project folder on the VPS
    [string]$Service = "",                       # empty = all services
    [switch]$SkipGitCheck                        # deploy even if GitHub is ahead of this PC
)

# Each step checks $LASTEXITCODE. (Don't use $ErrorActionPreference = "Stop": Windows
# PowerShell 5.1 would treat Docker's normal progress messages on stderr as errors.)
Set-Location $PSScriptRoot

# This script copies THIS PC's folder over the one on the VPS. If a teammate has
# pushed work that you have not pulled, deploying now would quietly put an older
# version on the server and undo their change - with nothing to show it happened.
# So: refuse when GitHub is ahead of this PC. Uncommitted work of your own is fine
# and is not checked, because deploying work in progress is the normal way to test.
# Skipped silently when git is not installed, this is not a repository, or the
# branch has never been pushed.
if (-not $SkipGitCheck -and (Test-Path (Join-Path $PSScriptRoot ".git"))) {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        # --verify --quiet: says whether there are any commits yet without
        # printing a scary message when there are not.
        $null = & git rev-parse --verify --quiet HEAD
        $branch = if ($LASTEXITCODE -eq 0) { & git rev-parse --abbrev-ref HEAD } else { "" }
        $remote = if ($branch) { & git config "branch.$branch.remote" } else { "" }
        if ($branch -and $LASTEXITCODE -eq 0 -and $remote) {
            & git fetch --quiet $remote $branch
            $behind = & git rev-list --count "HEAD..$remote/$branch"
            if ($LASTEXITCODE -eq 0 -and [int]$behind -gt 0) {
                throw ("GitHub is $behind commit(s) ahead of this PC. Deploying now would " +
                       "overwrite that work on the server.`n" +
                       "  Run 'git pull --rebase' first, or .\deploy.ps1 -SkipGitCheck to deploy anyway.")
            }
        }
    }
}

$archive = Join-Path $env:TEMP "lecture-transcriber.tgz"
# .git is excluded too: the VPS runs the code, it does not need the history, and a
# repository folder has no business sitting in a deployed service directory.
tar -czf $archive --exclude=data --exclude=.env --exclude=node_modules --exclude=dist --exclude=__pycache__ --exclude=.vscode --exclude=.git .
if ($LASTEXITCODE -ne 0) { throw "tar failed" }

scp $archive "${Server}:/tmp/lecture-transcriber.tgz"
if ($LASTEXITCODE -ne 0) { throw "scp failed" }
Remove-Item $archive

# --no-overwrite-dir keeps the project folder's own owner/permissions as set up on the VPS.
ssh $Server "tar -xzf /tmp/lecture-transcriber.tgz --no-overwrite-dir -C $RemoteDir &&rm /tmp/lecture-transcriber.tgz && cd $RemoteDir && docker compose up -d --build $Service && docker compose ps"
if ($LASTEXITCODE -ne 0) { throw "remote build/start failed" }
