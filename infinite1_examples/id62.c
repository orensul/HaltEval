
int id61();

int id62(){
  int x = id61();
  int y = id61();

  int* p = &y;
  int* q = &x;
  int b = 1;

  while (x < 100 && 100 < y && b) {
    if (p == q) {
      int k = id61(id61(), id61());
      (*p)++;
      while ((k--) > 100) {
        if (id61()) {
          p = &y;
        }
        if (id61()) {
          p = &x;
        }
        if (!b) {
          k++;
        }
      }
    } else {
      (*q)--;
      (*p)--;
      if (id61()) {
        p = &y;
      }
      if (id61()) {
        p = &x;
      }
    }
    b = id61();
  }
  return (0);
}