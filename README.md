# ddlc-poem-assistant
A small desktop tool for the poem minigame in *Doki Doki Literature Club Plus!* (and normal ddlc, not limited to plus AND not even limited to pirated, on *ALL* versions..) . It reads the word tiles on your screen, looks them up against the game's actual word/point table, and tells you which visible word scores the most for whichever character you're trying to favor.
Photo of how the scan screen feature works :

<img width="1919" height="1079" alt="Screenshot 2026-09-15 195622" src="https://github.com/user-attachments/assets/53f4d246-d463-42e7-af11-74c7a1d3f065" />
Video of the auto-play in action, set to 0.3s. :
https://github.com/user-attachments/assets/ff3cf96d-f8a8-48e8-94e8-32613c63ec71

incase you don't understand what it means by "switching to ddlc window", it means to CLICK onto ddlc so the game is focused, if you don't do this, it'll stop on the final round (haven't tested, probably the case.)

to start, click the green button named code, download zip, extract then do th instructions below (as you need ALL the files linked in the repo)

to make this work, download these things :
1. tesseract - https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/tesseract-ocr-w64-setup-5.5.3.20260724.exe (used for reading text)
2. python - search up python download idk
3. then open cmd prompt, and enter this to install the requirements. pip install -r requirements.txt
4. to run it, just open cmd prompt in the folder with the poem file and type in 'python ddlc_poem_assistant.py' (or just double click the py file, idk. both methods work))
