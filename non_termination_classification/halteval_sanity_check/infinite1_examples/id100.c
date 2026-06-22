int id100(int b1, int b2, int b3, int b4){
  int x = 0;
  if (b1) {
  retry:
    if (b2) {
      x++;
    }
    if (b2) {
      x++;
    }
  }
  if (b3) {
    if (b2) {
      x++;
    }
    if (b4) {
      goto retry;
    }
  }
}