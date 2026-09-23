param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$image = [System.Drawing.Image]::FromFile($Source)
try {
    $scale = 2
    $width = [Math]::Max(1, $image.Width * $scale)
    $height = [Math]::Max(1, $image.Height * $scale)
    $bitmap = New-Object System.Drawing.Bitmap($width, $height)
    try {
        $bitmap.SetResolution(192, 192)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.Clear([System.Drawing.Color]::Transparent)
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
            $graphics.DrawImage($image, 0, 0, $width, $height)
        }
        finally {
            $graphics.Dispose()
        }
        $bitmap.Save($Destination, [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally {
        $bitmap.Dispose()
    }
}
finally {
    $image.Dispose()
}
