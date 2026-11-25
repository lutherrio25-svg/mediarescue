param(
  [string]$Match = "entry_gui",
  [string]$ProcessName = "",
  [string]$FindTitle = "",
  [string]$Out = "C:\Users\STAR Corps\Documents\mediarescue\packaging\brand\out\ui.png"
)
# PrintWindow on the app's own HWND: captures the window even when another window is on top, which
# is what you want on a desktop session where the assistant cannot move the mouse.
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32Shot {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hwnd, int cmd);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc callback, IntPtr extra);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr hwnd, System.Text.StringBuilder text, int count);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
  public delegate bool EnumProc(IntPtr hwnd, IntPtr extra);
}
"@
function Find-WindowByTitle([string]$pattern) {
  # The About box is a Toplevel of the same process, so it is not anyone's MainWindow: walk the
  # desktop windows and match the caption instead.
  $script:found = [IntPtr]::Zero
  $callback = [Win32Shot+EnumProc]{
    param($wnd, $extra)
    if ([Win32Shot]::IsWindowVisible($wnd)) {
      $buffer = New-Object System.Text.StringBuilder 512
      [void][Win32Shot]::GetWindowTextW($wnd, $buffer, 512)
      if ($buffer.ToString() -match $pattern) { $script:found = $wnd; return $false }
    }
    return $true
  }
  [void][Win32Shot]::EnumWindows($callback, [IntPtr]::Zero)
  return $script:found
}
$hwnd = [IntPtr]::Zero
$proc = $null
if ($ProcessName -ne "") {
  # the installed app and a source run share the same caption: pick the one by executable name
  $proc = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
  if (-not $proc) { Write-Output "no window for process '$ProcessName'"; exit 1 }
  $hwnd = $proc.MainWindowHandle
} elseif ($FindTitle -ne "") {
  $hwnd = Find-WindowByTitle $FindTitle
  if ($hwnd -eq [IntPtr]::Zero) { Write-Output "no window with title '$FindTitle'"; exit 1 }
} else {
  # newest first: an older window of the same app must not be the one that gets photographed
  $proc = Get-Process | Where-Object {
    $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -match $Match
  } | Sort-Object StartTime -Descending | Select-Object -First 1
  if (-not $proc) { Write-Output "no window matched '$Match'"; exit 1 }
  $hwnd = $proc.MainWindowHandle
}
[void][Win32Shot]::ShowWindow($hwnd, 5)
[void][Win32Shot]::SetForegroundWindow($hwnd)
Start-Sleep -Milliseconds 400
$rect = New-Object Win32Shot+RECT
[void][Win32Shot]::GetWindowRect($hwnd, [ref]$rect)
$w = $rect.Right - $rect.Left
$h = $rect.Bottom - $rect.Top
if ($w -lt 400 -or $h -lt 300) { Write-Output "hwnd $hwnd is only ${w}x${h} - not the app window"; exit 2 }
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$gfx = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $gfx.GetHdc()
# 2 = PW_RENDERFULLCONTENT, needed for a DWM-composited window
[void][Win32Shot]::PrintWindow($hwnd, $hdc, 2)
$gfx.ReleaseHdc($hdc)
$gfx.Dispose()
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Output "hwnd $hwnd -> $Out ($w x $h)"
