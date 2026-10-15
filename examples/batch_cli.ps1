param([Parameter(Mandatory=$true)][string]$InputFolder,
      [Parameter(Mandatory=$true)][string]$OutputFolder)
python -m pdf_md_convert batch $InputFolder --recursive --output-dir $OutputFolder
exit $LASTEXITCODE
