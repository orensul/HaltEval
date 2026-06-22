void id16(int* x, int y){
  int* z = x;
  
  if (x != &y)
    while (y < 100) {
      y++;
      (*z)--;
    }
}