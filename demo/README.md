# Replay and regression spectra

These three measured Avantes exports are included with the maintainer's
permission. They represent separate PECVD SiO₂-on-Si wafers. The filenames use
Lithuanian labels for first, second and third.

At 400–850 nm with 100–1200 nm bounds, the published-index model gives about
528.207, 513.865 and 506.040 nm. Reproducing these checks software consistency,
not independent absolute accuracy.

Open a file with **File → Open measurement**, or from the project root:

```powershell
.\.venv\Scripts\python.exe .\run.py --replay '.\demo\Si-SiO2 pirmas_1212121U1.TXT'
```

Replay needs no spectrometer, vendor DLL or USB driver. Sample counts, dark and
reference are retained so normalization can be reproduced.
