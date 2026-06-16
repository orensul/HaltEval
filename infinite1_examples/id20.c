void id20(int y){
  int x = 0;
  while (y < 100)
    if (y < 50) {
      x++;
      y = 50;
    } else
      y++;
}