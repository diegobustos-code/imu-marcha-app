"""Punto de entrada del ejecutable de Windows."""
import multiprocessing

from imu_marcha_pc.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
