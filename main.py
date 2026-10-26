"""
Punctul de intrare principal pentru KindleBatchConverter.
Lansează aplicația GUI modernă CustomTkinter.
"""

import sys
import multiprocessing

from app.ui.main_window import MainWindow


def main():
    # Asigură suport corespunzător pentru multiprocessing pe Windows când este împachetat
    multiprocessing.freeze_support()

    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()

